"""
Diagnostic Agent — engine-agnostic NL2SQL engine for DBA health analytics.

Query execution tiers
---------------------
  Tier 1  : Keyword template dispatch — canonical validated SQL for known DBA
             intents. Zero LLM involvement, zero hallucination risk.
  Tier 2  : Vanna RAG + LLM generation — for novel / ad-hoc queries not
             covered by a template.
  Tier 3  : Self-correction loop — on a SQL error, fetch real column names from
             the engine's catalog, ask the LLM to rewrite the SQL (max 2 retries).
             Vanna is NOT trained on corrected SQL to avoid poisoning the store.

Seams (OmniDBA v3)
------------------
  LLM     : llm_provider.get_chat_llm()  → Ollama (local) or Vertex/Gemini.
  Engine  : db_adapter.get_adapter()     → Oracle (full) or Postgres (diag-only).

Runtime backend switching (v3 UI)
---------------------------------
  Every backend-specific resource (adapter, DB connection, Vanna store, Tier-3
  correction chain) is built lazily and CACHED by (provider, engine), so the
  Streamlit UI can flip Vertex↔Ollama and Oracle↔Postgres live without a
  process restart. `run_diagnostic_query(query, provider=, engine=)` selects the
  combo; omitting them falls back to the LLM_PROVIDER / DB_ENGINE env defaults,
  which preserves the original import-time behaviour for api.py / startup.sh.

NL2SQL layer : Vanna.ai with ChromaDB vector store (local RAG retrieval).
NOTE: the Tier-2 Vanna generator stays Ollama-backed (local RAG) on both
providers; Tier-1 templates cover every headline demo query deterministically,
so the Vertex path never depends on Ollama for the canned demo.
"""

import os
import re

from langchain_core.prompts import ChatPromptTemplate

from vanna.legacy.ollama.ollama import Ollama
from vanna.legacy.chromadb.chromadb_vector import ChromaDB_VectorStore

from llm_provider import get_chat_llm
from db_adapter import get_adapter


_OLLAMA_HOST  = os.getenv("OLLAMA_HOST",  "http://localhost:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")


# ===========================================================================
# Vanna — Tier 2 NL2SQL (local ChromaDB RAG + Ollama generation)
# ===========================================================================

class OmniDBAVanna(ChromaDB_VectorStore, Ollama):
    def __init__(self, config=None):
        ChromaDB_VectorStore.__init__(self, config=config)
        Ollama.__init__(self, config=config)


def _chroma_path(engine: str) -> str:
    """Per-engine vector store — keep Oracle and Postgres RAG stores separate."""
    if engine == "postgres":
        return os.getenv("CHROMA_PATH_PG", "./chroma_db_pg")
    return os.getenv("CHROMA_PATH", "./chroma_db")


# ===========================================================================
# Per-backend resource cache
# ===========================================================================
# Resources shared across providers for a given engine (adapter, connection,
# templates, Vanna store) are cached by ENGINE. The Tier-3 correction chain
# depends on the LLM too, so it is cached by (PROVIDER, ENGINE).

_RES_CACHE: dict[str, dict]   = {}
_CHAIN_CACHE: dict[tuple, object] = {}


def _get_resources(engine: str) -> dict:
    """Adapter + live connection + Tier-1 templates + Vanna store for `engine`."""
    if engine not in _RES_CACHE:
        adapter = get_adapter(engine)
        _RES_CACHE[engine] = {
            "adapter":    adapter,
            "templates":  adapter.templates(),
            "connection": adapter.connect(),
            "vn":         OmniDBAVanna(config={
                "ollama_host": _OLLAMA_HOST,
                "model":       _OLLAMA_MODEL,
                "path":        _chroma_path(engine),
            }),
        }
    return _RES_CACHE[engine]


def _get_correction_chain(provider: str, engine: str, adapter):
    """Tier-3 self-correction chain: engine-aware prompt piped to the provider LLM."""
    key = (provider, engine)
    if key not in _CHAIN_CACHE:
        llm = get_chat_llm(provider=provider, temperature=0)
        engine_name = adapter.engine.capitalize()
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                f"You are a {engine_name} SQL expert. The SQL below failed with a "
                f"database error. Rewrite it so it runs correctly using ONLY the real "
                f"column names listed. Return ONLY the corrected SQL — no explanation, "
                f"no markdown fences.",
            ),
            (
                "human",
                "Database error:\n{error}\n\n"
                "Failed SQL:\n{bad_sql}\n\n"
                "Real columns available in referenced tables:\n{schema_context}",
            ),
        ])
        _CHAIN_CACHE[key] = prompt | llm
    return _CHAIN_CACHE[key]


# ===========================================================================
# Tier 1 — template matching
# ===========================================================================

def _match_template(query: str, templates: dict) -> str | None:
    """
    Score each template against the lowercased query.
    Returns the key with the highest score (min 1), or None if no match.
    Multi-word phrases in keyword groups are checked before single words
    so 'redo log' beats 'log' alone.
    """
    q = query.lower()
    best_key, best_score = None, 0

    for key, (_sql, groups) in templates.items():
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
# Tier 3 helpers
# ===========================================================================

def _extract_table_names(sql: str) -> list[str]:
    """Regex extraction of table/view names following FROM and JOIN keywords."""
    hits = re.findall(
        r'\b(?:FROM|JOIN)\s+([a-zA-Z0-9_$#]+(?:\.[a-zA-Z0-9_$#]+)?)',
        sql, flags=re.IGNORECASE,
    )
    names = [h.split(".")[-1].upper() for h in hits]
    return list(dict.fromkeys(names))  # deduplicate, preserve order


def _correct_sql(bad_sql: str, error_msg: str, adapter, chain) -> str:
    """Ask the LLM to rewrite failing SQL grounded by the real engine schema."""
    tables     = _extract_table_names(bad_sql)
    schema_ctx = adapter.schema_context(tables)
    response   = chain.invoke({
        "error":          error_msg,
        "bad_sql":        bad_sql,
        "schema_context": schema_ctx,
    })
    corrected = response.content.strip()
    corrected = re.sub(r"^```(?:sql)?\s*", "", corrected, flags=re.IGNORECASE)
    corrected = re.sub(r"\s*```$", "", corrected)
    return corrected.strip()


# ===========================================================================
# Schema / RAG training
# ===========================================================================

def train_schema(engine: str | None = None) -> None:
    """Seed the ChromaDB vector store for Tier-2 retrieval (for `engine`)."""
    engine = (engine or os.getenv("DB_ENGINE", "oracle")).lower()
    res     = _get_resources(engine)
    adapter, vn = res["adapter"], res["vn"]

    if adapter.engine == "oracle":
        _train_oracle_ddl(res["connection"], vn)

    for item in adapter.curated_queries():
        vn.train(question=item["question"], sql=item["sql"])


def _train_oracle_ddl(connection, vn) -> None:
    """Ingest Oracle catalog-view DDL into the Vanna store (Oracle-only)."""
    import oracledb  # noqa: PLC0415

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


# Backward-compatible alias — startup.sh imports this name.
train_on_oracle_schema = train_schema


# ===========================================================================
# Public API
# ===========================================================================

def run_diagnostic_query(user_query: str,
                         provider: str | None = None,
                         engine: str | None = None):
    """
    Execute a natural-language DBA query through the three-tier pipeline against
    the chosen (provider, engine) backend.

    provider / engine
        None → LLM_PROVIDER / DB_ENGINE env defaults (original behaviour).

    Returns : (sql, column_names, rows)
    Raises  : RuntimeError when all retries are exhausted.
    """
    provider = (provider or os.getenv("LLM_PROVIDER", "vertex")).lower()
    engine   = (engine   or os.getenv("DB_ENGINE",    "oracle")).lower()

    res        = _get_resources(engine)
    adapter    = res["adapter"]
    templates  = res["templates"]
    connection = res["connection"]
    vn         = res["vn"]

    # ── Tier 1 ──────────────────────────────────────────────────────────────
    template_key = _match_template(user_query, templates)
    if template_key:
        sql       = templates[template_key][0]
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
            data = [adapter.normalize_row(row) for row in raw]
            break  # success
        except adapter.error_types as e:
            last_error = str(e)
            if attempt < 2:
                chain     = _get_correction_chain(provider, engine, adapter)
                sql       = _correct_sql(sql, last_error, adapter, chain)
                corrected = True
            else:
                raise RuntimeError(
                    f"SQL failed after {attempt} correction attempt(s).\n\n"
                    f"Last database error: {last_error}\n\n"
                    f"Last SQL tried:\n{sql}"
                ) from e

    # ── Train Vanna only on clean Tier-2 SQL (not templates, not corrections) ─
    if from_tier == 2 and not corrected:
        vn.train(question=user_query, sql=sql)

    return sql, columns, data


# ===========================================================================
# Backward-compatible module-level connection (env-default engine)
# ===========================================================================
# api.py does `from diagnostic_agent import connection` for its Oracle
# health-report / custom-SQL paths. Preserve it as the default-engine connection.
connection = _get_resources(os.getenv("DB_ENGINE", "oracle").lower())["connection"]
