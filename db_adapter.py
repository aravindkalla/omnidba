"""
Database adapter seam — turns the "Oracle DBA agent" into OmniDBA.

Why this exists
---------------
v1 hard-wired `oracledb.connect(...)` and an Oracle-only `_TEMPLATES` dict in
diagnostic_agent.py. v3 abstracts BOTH behind a `DBAdapter` so the engine-agnostic
3-tier NL2SQL pipeline (template → RAG → self-correct) serves any engine:

    DB_ENGINE=oracle    → full path: diagnostics + RMAN backup HITL
    DB_ENGINE=postgres  → diagnostics only (demoable multi-DB proof, innovation #5)

Write operations (RMAN) stay Oracle-only for the hackathon. Postgres/SQL Server/
Mongo write adapters are documented roadmap (see docs/GCP_REBUILD_RUNBOOK.md §7).

Contract
--------
Each adapter provides:
    connect()               → a live DB-API connection (cursor() supported)
    templates()             → {key: (sql, keyword_groups)} for Tier-1 dispatch
    schema_context(tables)  → plain-text column listing for Tier-3 self-correction
    quote_param             → the driver's bind-parameter style ("named" vs "format")

Usage
-----
    from db_adapter import get_adapter
    adapter = get_adapter()
    conn    = adapter.connect()
    tmpls   = adapter.templates()
"""

import os
from abc import ABC, abstractmethod


class DBAdapter(ABC):
    """Interface every supported engine implements."""

    engine: str = "abstract"

    @abstractmethod
    def connect(self):
        """Return a live DB-API 2.0 connection. Caller manages cursor lifecycle."""

    @abstractmethod
    def templates(self) -> dict:
        """Tier-1 canonical SQL: {key: (sql_str, [[kw, ...], ...])}."""

    @abstractmethod
    def schema_context(self, table_names: list[str]) -> str:
        """Real column names/types for the given tables, as a prompt-ready block.
        Feeds Tier-3 self-correction so the LLM rewrites against the true schema."""

    def supports_backup(self) -> bool:
        """Whether this engine has a write/backup path wired (HITL-gated)."""
        return False

    @property
    def error_types(self) -> tuple:
        """Exception class(es) that represent a SQL execution error for this
        engine. Drives the Tier-3 self-correction retry loop — the pipeline
        catches these to trigger a schema-grounded rewrite, and lets anything
        else propagate."""
        return (Exception,)

    def normalize_row(self, row) -> tuple:
        """Coerce a result row's cell values into plain, JSON/msgpack-
        serialisable Python types. Override where the driver returns exotic
        objects (e.g. Oracle LOB handles that must be read while the cursor is
        open). The LangGraph checkpointer requires plain types."""
        return tuple(row)

    def curated_queries(self) -> list:
        """Curated question→SQL pairs used to seed the Tier-2 Vanna store."""
        return []


# ===========================================================================
# Oracle — full path (diagnostics + RMAN backup HITL)
# ===========================================================================

class OracleAdapter(DBAdapter):
    engine = "oracle"

    def connect(self):
        import oracledb
        return oracledb.connect(
            user=os.getenv("ORACLE_USER", "admin"),
            password=os.getenv("ORACLE_PASSWORD"),
            dsn=os.getenv("ORACLE_DSN", "localhost:1521/FREEPDB1"),
        )

    def templates(self) -> dict:
        # Lifted verbatim from v1 diagnostic_agent.py _TEMPLATES.
        # TODO(build-day): create templates_oracle.py by extracting that dict.
        from templates_oracle import TEMPLATES
        return TEMPLATES

    def schema_context(self, table_names: list[str]) -> str:
        if not table_names:
            return "No tables identified in the SQL."
        lines: list[str] = []
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                for table in table_names:
                    try:
                        cur.execute(
                            "SELECT column_name, data_type FROM all_tab_columns "
                            "WHERE table_name = :t ORDER BY column_id",
                            t=table.upper(),
                        )
                        cols = cur.fetchall()
                        if cols:
                            lines.append(
                                f"{table}: " + ", ".join(f"{c[0]} ({c[1]})" for c in cols)
                            )
                    except Exception:
                        pass
        finally:
            conn.close()
        return "\n".join(lines) if lines else "Schema lookup returned no results."

    def supports_backup(self) -> bool:
        return True

    @property
    def error_types(self) -> tuple:
        import oracledb
        return (oracledb.DatabaseError,)

    def normalize_row(self, row) -> tuple:
        import oracledb
        # LOB handles must be read while the cursor is open and are not
        # msgpack-serialisable — the LangGraph checkpointer needs plain types.
        return tuple(
            v.read() if isinstance(v, oracledb.LOB) else v for v in row
        )

    def curated_queries(self) -> list:
        from templates_oracle import CURATED_QUERIES
        return CURATED_QUERIES


# ===========================================================================
# Postgres — diagnostics-only (multi-DB demo target, port 5433)
# ===========================================================================

class PostgresAdapter(DBAdapter):
    engine = "postgres"

    def connect(self):
        # TODO(build-day): pip install "psycopg[binary]" (requirements-gcp.txt §5)
        import psycopg
        return psycopg.connect(
            host=os.getenv("PG_HOST", "localhost"),
            port=os.getenv("PG_PORT", "5433"),
            dbname=os.getenv("PG_DB", "omnidba"),
            user=os.getenv("PG_USER", "omnidba"),
            password=os.getenv("PG_PASSWORD"),
        )

    def templates(self) -> dict:
        # Postgres equivalents of the headline Oracle templates.
        # TODO(build-day): create templates_postgres.py (see runbook §7 mapping).
        from templates_postgres import TEMPLATES
        return TEMPLATES

    def schema_context(self, table_names: list[str]) -> str:
        if not table_names:
            return "No tables identified in the SQL."
        lines: list[str] = []
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                for table in table_names:
                    cur.execute(
                        "SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_name = %s ORDER BY ordinal_position",
                        (table.lower(),),
                    )
                    cols = cur.fetchall()
                    if cols:
                        lines.append(
                            f"{table}: " + ", ".join(f"{c[0]} ({c[1]})" for c in cols)
                        )
        finally:
            conn.close()
        return "\n".join(lines) if lines else "Schema lookup returned no results."

    @property
    def error_types(self) -> tuple:
        import psycopg
        return (psycopg.Error,)

    def curated_queries(self) -> list:
        from templates_postgres import CURATED_QUERIES
        return CURATED_QUERIES


# ===========================================================================
# Factory
# ===========================================================================

_ADAPTERS: dict[str, type[DBAdapter]] = {
    "oracle":   OracleAdapter,
    "postgres": PostgresAdapter,
    # Roadmap (documented, not built): "sqlserver", "mysql", "mongodb"
}


def get_adapter(engine: str | None = None) -> DBAdapter:
    """Return the adapter for `engine`, or the DB_ENGINE default (oracle).

    Passing `engine` explicitly lets the UI build adapters for a backend other
    than the process default (the live Oracle↔Postgres toggle)."""
    engine = (engine or os.getenv("DB_ENGINE", "oracle")).lower()
    try:
        return _ADAPTERS[engine]()
    except KeyError:
        raise ValueError(
            f"Unknown DB_ENGINE={engine!r}. Supported: {sorted(_ADAPTERS)}."
        )


if __name__ == "__main__":
    # Quick sanity check: python db_adapter.py
    a = get_adapter()
    print(f"Active DB engine: {a.engine} (backup path: {a.supports_backup()})")
