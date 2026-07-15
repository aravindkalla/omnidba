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
            The adapter supplies the live connection, Tier-1 templates, schema
            introspection, engine-specific error types, row normalisation
            (e.g. Oracle LOB), and curated Q→SQL training pairs.

NL2SQL layer : Vanna.ai with ChromaDB vector store (local RAG retrieval).
Embeddings   : ChromaDB default (all-MiniLM-L6-v2, ONNX, pre-cached).

NOTE: The Tier-2 Vanna generator remains Ollama-backed (local RAG). Tier-1
templates cover every headline demo query deterministically on both engines, so
the Vertex path never depends on Ollama for the canned demo. Migrating Vanna's
generator to Vertex is tracked as a follow-up (see docs/GCP_REBUILD_RUNBOOK.md).
"""

import os
import re

from langchain_core.prompts import ChatPromptTemplate
from vanna.legacy.ollama.ollama import Ollama
from vanna.legacy.chromadb.chromadb_vector import ChromaDB_VectorStore

from llm_provider import get_chat_llm
from db_adapter import get_adapter


# ===========================================================================
# Engine adapter — the single DB seam
# ===========================================================================
# get_adapter() reads DB_ENGINE (oracle|postgres). The module-level `connection`
# is kept for backward compatibility: api.py imports it directly for its
# Oracle health-report / custom-SQL paths. With DB_ENGINE=oracle this reproduces
# v1 exactly (a live oracledb connection opened at import time).

_adapter   = get_adapter()
_TEMPLATES = _adapter.templates()

connection = _adapter.connect()


# ===========================================================================
# Tier 1 — Canonical SQL templates (loaded from the active engine's adapter)
# Each entry: key → (sql, keyword_groups)
# Scoring: +1 per keyword_group that has at least one word present in query.
# A template wins if it has the highest score (minimum 1).
# ===========================================================================

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
# Vanna — Tier 2 NL2SQL (local ChromaDB RAG + Ollama generation)
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
# Tier 3 — Self-correction LLM chain (routed through the provider seam)
# ===========================================================================

_correction_llm = get_chat_llm(temperature=0)

_ENGINE_NAME = _adapter.engine.capitalize()

_CORRECTION_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        f"You are a {_ENGINE_NAME} SQL expert. The SQL below failed with a "
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

_correction_chain = _CORRECTION_PROMPT | _correction_llm


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


def _correct_sql(bad_sql: str, error_msg: str) -> str:
    """Ask the LLM to rewrite failing SQL grounded by the real engine schema."""
    tables     = _extract_table_names(bad_sql)
    schema_ctx = _adapter.schema_context(tables)
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
# Schema / RAG training
# ===========================================================================

def train_schema() -> None:
    """
    Seed the ChromaDB vector store for Tier-2 retrieval.

    For Oracle, live DDL is ingested via DBMS_METADATA for richer grounding.
    On every engine, the adapter's curated Q→SQL pairs (aligned to the Tier-1
    templates) are trained so Vanna retrieves verified examples for novel
    phrasings.
    """
    if _adapter.engine == "oracle":
        _train_oracle_ddl()

    for item in _adapter.curated_queries():
        vn.train(question=item["question"], sql=item["sql"])


def _train_oracle_ddl() -> None:
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

def run_diagnostic_query(user_query: str):
    """
    Execute a natural-language DBA query through the three-tier pipeline.

    Tier 1  — keyword template dispatch  (deterministic, no LLM)
    Tier 2  — Vanna RAG + LLM            (novel queries)
    Tier 3  — self-correction loop       (SQL error → schema-grounded LLM fix, max 2 retries)

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
            # Adapter normalises driver-specific cell types (e.g. Oracle LOB)
            # into plain, checkpointer-serialisable values.
            data = [_adapter.normalize_row(row) for row in raw]
            break  # success
        except _adapter.error_types as e:
            last_error = str(e)
            if attempt < 2:
                sql       = _correct_sql(sql, last_error)
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
