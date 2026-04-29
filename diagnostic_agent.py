"""
Diagnostic Agent — NL2SQL engine for Oracle AWR/ASH/health analytics.

Query execution tiers
---------------------
  Tier 1  : Keyword template dispatch — canonical validated SQL for known DBA
             intents. Zero LLM involvement, zero hallucination risk.
  Tier 2  : Vanna RAG + Ollama generation — for novel / ad-hoc queries not
             covered by a template.
  Tier 3  : Self-correction loop — on ORA-XXXXX error, fetch real column names
             from ALL_TAB_COLUMNS, ask the LLM to rewrite the SQL (max 2 retries).
             Vanna is NOT trained on corrected SQL to avoid poisoning the vector store.

LLM backend  : Ollama (fully local, zero external API calls)
NL2SQL layer : Vanna.ai with ChromaDB vector store (local)
Embeddings   : ChromaDB default (all-MiniLM-L6-v2, ONNX, pre-cached)
DB driver    : python-oracledb (thin mode, no Oracle Client required)
"""

import os
import re
import oracledb

from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from vanna.legacy.ollama.ollama import Ollama
from vanna.legacy.chromadb.chromadb_vector import ChromaDB_VectorStore


# ===========================================================================
# Tier 1 — Canonical SQL templates
# Validated against Oracle 26ai Free.  Only real column names used.
# Each entry: (sql, keyword_groups)
# Scoring: +1 per keyword_group that has at least one word present in query.
# A template wins if it has the highest score (minimum 1).
# ===========================================================================

_TABLESPACE_SQL = (
    "SELECT t.tablespace_name, "
    "       t.total_bytes, "
    "       NVL(f.free_bytes, 0) AS free_bytes, "
    "       t.total_bytes - NVL(f.free_bytes, 0) AS used_bytes, "
    "       ROUND((t.total_bytes - NVL(f.free_bytes, 0)) * 100 / t.total_bytes, 2) AS pct_used "
    "FROM "
    "    (SELECT tablespace_name, SUM(bytes) AS total_bytes "
    "     FROM dba_data_files GROUP BY tablespace_name) t "
    "LEFT JOIN "
    "    (SELECT tablespace_name, SUM(bytes) AS free_bytes "
    "     FROM dba_free_space GROUP BY tablespace_name) f "
    "  ON t.tablespace_name = f.tablespace_name "
    "ORDER BY pct_used DESC"
)

_TOP_SQL_ELAPSED = (
    "SELECT st.sql_id, "
    "       SUBSTR(tx.sql_text, 1, 80) AS sql_text, "
    "       st.elapsed_time_total, "
    "       st.executions_total, "
    "       ROUND(st.elapsed_time_total / NULLIF(st.executions_total, 0) / 1000000, 2) AS avg_elapsed_sec "
    "FROM dba_hist_sqlstat st "
    "JOIN dba_hist_snapshot sn "
    "  ON st.snap_id = sn.snap_id "
    " AND st.dbid = sn.dbid "
    " AND st.instance_number = sn.instance_number "
    "JOIN dba_hist_sqltext tx ON st.sql_id = tx.sql_id AND st.dbid = tx.dbid "
    "WHERE sn.end_interval_time >= SYSDATE - INTERVAL '1' HOUR "
    "ORDER BY st.elapsed_time_total DESC "
    "FETCH FIRST 10 ROWS ONLY"
)

_BLOCKING_SESSIONS = (
    "SELECT b.sid AS blocking_sid, "
    "       b.serial# AS blocking_serial, "
    "       b.username AS blocking_user, "
    "       b.status AS blocking_status, "
    "       w.sid AS waiting_sid, "
    "       w.serial# AS waiting_serial, "
    "       w.username AS waiting_user, "
    "       w.wait_class, "
    "       w.seconds_in_wait "
    "FROM v$session w "
    "JOIN v$session b ON w.blocking_session = b.sid "
    "WHERE w.blocking_session IS NOT NULL "
    "ORDER BY w.seconds_in_wait DESC"
)

_INVALID_OBJECTS = (
    "SELECT owner, object_name, object_type, last_ddl_time, status "
    "FROM dba_objects "
    "WHERE status = 'INVALID' "
    "ORDER BY owner, object_type, object_name"
)

_REDO_LOG_STATUS = (
    "SELECT l.group#, l.members, "
    "       ROUND(l.bytes / 1048576, 1) AS size_mb, "
    "       l.status, l.archived, lf.member AS log_file "
    "FROM v$log l "
    "JOIN v$logfile lf ON l.group# = lf.group# "
    "ORDER BY l.group#"
)

_ACTIVE_SESSIONS = (
    "SELECT sid, serial#, username, status, machine, program, "
    "       wait_class, event, seconds_in_wait, sql_id "
    "FROM v$session "
    "WHERE type = 'USER' AND username IS NOT NULL "
    "ORDER BY seconds_in_wait DESC NULLS LAST"
)

_RMAN_BACKUP_HISTORY = (
    "SELECT session_key, input_type, status, "
    "       TO_CHAR(start_time, 'YYYY-MM-DD HH24:MI') AS start_time, "
    "       TO_CHAR(end_time,   'YYYY-MM-DD HH24:MI') AS end_time, "
    "       elapsed_seconds, "
    "       input_bytes_display, output_bytes_display, "
    "       output_device_type "
    "FROM v$rman_backup_job_details "
    "ORDER BY start_time DESC "
    "FETCH FIRST 20 ROWS ONLY"
)

# Template registry: key → (sql, keyword_groups)
# keyword_groups: list of lists — one hit per inner list scores +1
_TEMPLATES: dict[str, tuple[str, list[list[str]]]] = {
    "tablespace_usage": (
        _TABLESPACE_SQL,
        [
            ["tablespace", "tablespaces"],
            ["usage", "utilization", "space", "full", "capacity", "used", "free", "pct", "percent"],
        ],
    ),
    "top_sql_elapsed": (
        _TOP_SQL_ELAPSED,
        [
            ["sql", "query", "queries", "statement"],
            ["top", "slow", "worst", "elapsed", "performance", "cpu", "long", "time", "expensive"],
        ],
    ),
    "blocking_sessions": (
        _BLOCKING_SESSIONS,
        [
            ["block", "blocking", "blocked", "lock", "locked", "deadlock", "contention", "waiting"],
        ],
    ),
    "invalid_objects": (
        _INVALID_OBJECTS,
        [
            ["invalid", "broken", "compile", "compilation"],
            ["object", "objects", "procedure", "package", "view", "function", "trigger"],
        ],
    ),
    "redo_log_status": (
        _REDO_LOG_STATUS,
        [
            ["redo", "archivelog", "archive", "log group", "redo log"],
        ],
    ),
    "active_sessions": (
        _ACTIVE_SESSIONS,
        [
            ["active", "current", "connected", "who is", "who are"],
            ["session", "sessions", "user", "users", "connection", "connections"],
        ],
    ),
    "rman_backup_history": (
        _RMAN_BACKUP_HISTORY,
        [
            ["list", "show", "display", "history", "recent", "last", "previous", "status"],
            ["rman", "backup", "backups", "backup job", "backup jobs", "backup history"],
        ],
    ),
}


def _match_template(query: str) -> str | None:
    """
    Score each template against the lowercased query.
    Returns the key with the highest score (min 1), or None if no match.
    Multi-word phrases in keyword groups are checked before single words
    so 'redo log' beats 'log' alone.
    """
    q = query.lower()
    best_key, best_score = None, 0

    for key, (_sql, groups) in _TEMPLATES.items():
        score = 0
        for group in groups:
            for kw in sorted(group, key=len, reverse=True):
                if kw in q:
                    score += 1
                    break
        if score > best_score:
            best_score = score
            best_key = key

    return best_key if best_score >= 1 else None


# ===========================================================================
# Vanna — Tier 2 NL2SQL
# ===========================================================================

class OmniDBAVanna(ChromaDB_VectorStore, Ollama):
    def __init__(self, config=None):
        ChromaDB_VectorStore.__init__(self, config=config)
        Ollama.__init__(self, config=config)


_OLLAMA_HOST  = os.getenv("OLLAMA_HOST",  "http://localhost:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
_CHROMA_PATH  = os.getenv("CHROMA_PATH",  "./chroma_db")

vn = OmniDBAVanna(config={
    "ollama_host": _OLLAMA_HOST,
    "model":       _OLLAMA_MODEL,
    "path":        _CHROMA_PATH,
})

# ===========================================================================
# Tier 3 — Self-correction LLM chain
# ===========================================================================

_correction_llm = ChatOllama(model=_OLLAMA_MODEL, base_url=_OLLAMA_HOST, temperature=0)

_CORRECTION_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are an Oracle SQL expert. The SQL below failed with an Oracle error. "
        "Rewrite it so it runs correctly using ONLY the real column names listed. "
        "Return ONLY the corrected SQL — no explanation, no markdown fences.",
    ),
    (
        "human",
        "Oracle error:\n{error}\n\n"
        "Failed SQL:\n{bad_sql}\n\n"
        "Real columns available in referenced tables:\n{schema_context}",
    ),
])

_correction_chain = _CORRECTION_PROMPT | _correction_llm


# ===========================================================================
# Oracle connection
# ===========================================================================

connection = oracledb.connect(
    user     = os.getenv("ORACLE_USER",     "admin"),
    password = os.getenv("ORACLE_PASSWORD", "password"),
    dsn      = os.getenv("ORACLE_DSN",      "localhost/FREEPDB1"),
)


# ===========================================================================
# Tier 3 helpers
# ===========================================================================

def _extract_table_names(sql: str) -> list[str]:
    """Regex extraction of table/view names following FROM and JOIN keywords."""
    hits = re.findall(
        r'\b(?:FROM|JOIN)\s+([a-zA-Z0-9_$#]+(?:\.[a-zA-Z0-9_$#]+)?)',
        sql, flags=re.IGNORECASE,
    )
    # Strip schema prefix (SYS.DBA_DATA_FILES → DBA_DATA_FILES)
    names = [h.split(".")[-1].upper() for h in hits]
    return list(dict.fromkeys(names))  # deduplicate, preserve order


def _get_schema_context(table_names: list[str]) -> str:
    """
    Look up real column names + data types from ALL_TAB_COLUMNS.
    Returns a plain-text block injected into the correction prompt.
    """
    if not table_names:
        return "No tables identified in the SQL."

    lines = []
    with connection.cursor() as cursor:
        for table in table_names:
            try:
                cursor.execute(
                    "SELECT column_name, data_type "
                    "FROM all_tab_columns "
                    "WHERE table_name = :t "
                    "ORDER BY column_id",
                    t=table,
                )
                cols = cursor.fetchall()
                if cols:
                    col_list = ", ".join(f"{c[0]} ({c[1]})" for c in cols)
                    lines.append(f"{table}: {col_list}")
            except oracledb.DatabaseError:
                pass

    return "\n".join(lines) if lines else "Schema lookup returned no results."


def _correct_sql(bad_sql: str, error_msg: str) -> str:
    """Ask the LLM to rewrite failing SQL grounded by the real Oracle schema."""
    tables     = _extract_table_names(bad_sql)
    schema_ctx = _get_schema_context(tables)
    response   = _correction_chain.invoke({
        "error":          error_msg,
        "bad_sql":        bad_sql,
        "schema_context": schema_ctx,
    })
    corrected = response.content.strip()
    # Strip any markdown fences the LLM may add despite instructions
    corrected = re.sub(r"^```(?:sql)?\s*", "", corrected, flags=re.IGNORECASE)
    corrected = re.sub(r"\s*```$", "", corrected)
    return corrected.strip()


# ===========================================================================
# Schema training
# ===========================================================================

def train_on_oracle_schema() -> None:
    """
    One-time (or periodic) ingestion of Oracle DDL and curated Q→SQL pairs
    into the ChromaDB vector store.  Curated queries align with Tier 1 templates
    so Vanna retrieves verified examples for novel phrasings.
    """
    ddl_views = [
        "dba_hist_active_sess_history", "dba_hist_sqlstat", "dba_hist_snapshot",
        "dba_tablespaces", "dba_data_files", "dba_free_space",
        "dba_objects", "dba_indexes", "v$instance", "v$archive_dest", "v$log",
    ]
    with connection.cursor() as cursor:
        for view in ddl_views:
            try:
                cursor.execute(
                    "SELECT DBMS_METADATA.GET_DDL('VIEW', :v) FROM DUAL",
                    v=view.upper(),
                )
                row = cursor.fetchone()
                if row:
                    vn.train(ddl=str(row[0]))
            except oracledb.DatabaseError:
                pass

    curated_queries = [
        # Tablespace — multiple phrasings all point to the same canonical SQL
        {"question": "What is the current tablespace usage percentage?",  "sql": _TABLESPACE_SQL},
        {"question": "Show me tablespace utilization",                    "sql": _TABLESPACE_SQL},
        {"question": "How full are my tablespaces?",                      "sql": _TABLESPACE_SQL},
        {"question": "Which tablespaces are almost full or critical?",    "sql": _TABLESPACE_SQL},
        {"question": "Show tablespace space usage in GB",                 "sql": _TABLESPACE_SQL},
        # SQL performance
        {"question": "Show top 10 SQL by elapsed time in the last hour",  "sql": _TOP_SQL_ELAPSED},
        {"question": "What are the slowest queries right now?",           "sql": _TOP_SQL_ELAPSED},
        # Blocking
        {"question": "Show active blocking sessions right now",           "sql": _BLOCKING_SESSIONS},
        {"question": "Are there any lock contentions or deadlocks?",      "sql": _BLOCKING_SESSIONS},
        # Invalid objects
        {"question": "Are there any invalid database objects?",           "sql": _INVALID_OBJECTS},
        {"question": "List all invalid procedures and packages",          "sql": _INVALID_OBJECTS},
        # Redo logs
        {"question": "Show redo log group status and archiving",          "sql": _REDO_LOG_STATUS},
        # Active sessions
        {"question": "Who is connected to the database right now?",       "sql": _ACTIVE_SESSIONS},
        {"question": "Show all active user sessions",                     "sql": _ACTIVE_SESSIONS},
    ]
    for item in curated_queries:
        vn.train(question=item["question"], sql=item["sql"])


# ===========================================================================
# Public API
# ===========================================================================

def run_diagnostic_query(user_query: str):
    """
    Execute a natural-language DBA query through the three-tier pipeline.

    Tier 1  — keyword template dispatch  (deterministic, no LLM)
    Tier 2  — Vanna RAG + Ollama         (novel queries)
    Tier 3  — self-correction loop       (ORA error → schema-grounded LLM fix, max 2 retries)

    Returns : (sql, column_names, rows)
    Raises  : RuntimeError when all retries are exhausted.
    """
    # ── Tier 1 ──────────────────────────────────────────────────────────────
    template_key = _match_template(user_query)
    if template_key:
        sql       = _TEMPLATES[template_key][0]
        from_tier = 1
    else:
        # ── Tier 2 ──────────────────────────────────────────────────────────
        sql       = vn.generate_sql(user_query)
        from_tier = 2

    # ── Execute with Tier 3 self-correction ─────────────────────────────────
    corrected  = False
    last_error = None

    for attempt in range(3):  # attempt 0 = first try; 1-2 = LLM corrections
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                columns = [col[0] for col in cursor.description]
                raw     = cursor.fetchall()
            # LOB objects must be read while the cursor is open and are not
            # msgpack-serializable (LangGraph checkpointer requires plain types).
            data = [
                tuple(v.read() if isinstance(v, oracledb.LOB) else v for v in row)
                for row in raw
            ]
            break  # success
        except oracledb.DatabaseError as e:
            last_error = str(e)
            if attempt < 2:
                sql       = _correct_sql(sql, last_error)
                corrected = True
            else:
                raise RuntimeError(
                    f"SQL failed after {attempt} correction attempt(s).\n\n"
                    f"Last Oracle error: {last_error}\n\n"
                    f"Last SQL tried:\n{sql}"
                ) from e

    # ── Train Vanna only on clean Tier-2 SQL (not templates, not corrections) ─
    if from_tier == 2 and not corrected:
        vn.train(question=user_query, sql=sql)

    return sql, columns, data

