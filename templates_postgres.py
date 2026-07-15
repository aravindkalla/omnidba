"""
PostgreSQL Tier-1 diagnostic SQL templates.

Postgres equivalents of the headline Oracle templates (templates_oracle.py) so the
SAME natural-language question is answered against Postgres by the SAME agent —
the multi-DB "OmniDBA" proof (innovation #5). Loaded by
db_adapter.PostgresAdapter.templates().

Design choice: keyword_groups are kept IDENTICAL to the Oracle templates for the
shared intents, so the Tier-1 matcher routes the same phrasing the same way on
either engine. Postgres-specific synonyms (e.g. "wal") are added where they help.

Engine coverage vs Oracle
-------------------------
  tablespace_usage   → pg_tablespace + pg_tablespace_size()
  top_sql_elapsed    → pg_stat_statements   (requires the extension — see note)
  blocking_sessions  → pg_stat_activity + pg_blocking_pids()
  active_sessions    → pg_stat_activity WHERE state='active'
  invalid_objects    → pg_index WHERE indisvalid = false  (invalid indexes)
  redo_log_status    → pg_ls_waldir()       (WAL is Postgres's redo equivalent)
  rman_backup_history→ OMITTED — no RMAN in Postgres. Backup/write path is
                        Oracle-only for the hackathon; pgBackRest adapter = roadmap.

Validated against PostgreSQL 16 (Docker, localhost:5433).
"""

# ===========================================================================
# Canonical SQL
# ===========================================================================

TABLESPACE_SQL = (
    "SELECT spcname AS tablespace_name, "
    "       pg_tablespace_size(spcname) AS total_bytes, "
    "       pg_size_pretty(pg_tablespace_size(spcname)) AS size_pretty "
    "FROM pg_tablespace "
    "ORDER BY pg_tablespace_size(spcname) DESC"
)

# Requires: CREATE EXTENSION pg_stat_statements; and
# shared_preload_libraries='pg_stat_statements' in postgresql.conf (needs restart).
TOP_SQL_ELAPSED = (
    "SELECT queryid, "
    "       LEFT(query, 80) AS query_text, "
    "       total_exec_time, "
    "       calls, "
    "       ROUND((mean_exec_time / 1000.0)::numeric, 3) AS avg_elapsed_sec "
    "FROM pg_stat_statements "
    "ORDER BY total_exec_time DESC "
    "LIMIT 10"
)

BLOCKING_SESSIONS = (
    "SELECT ki.pid AS blocking_pid, "
    "       ki.usename AS blocking_user, "
    "       ki.state AS blocking_status, "
    "       bl.pid AS waiting_pid, "
    "       bl.usename AS waiting_user, "
    "       bl.wait_event_type, "
    "       bl.wait_event, "
    "       LEFT(bl.query, 80) AS waiting_query "
    "FROM pg_stat_activity bl "
    "JOIN LATERAL unnest(pg_blocking_pids(bl.pid)) AS blocking(pid) ON true "
    "JOIN pg_stat_activity ki ON ki.pid = blocking.pid "
    "ORDER BY bl.pid"
)

# Postgres has no Oracle-style INVALID objects; the closest real analog is an
# index left invalid after a failed CREATE INDEX CONCURRENTLY.
INVALID_OBJECTS = (
    "SELECT n.nspname AS schema_name, "
    "       t.relname AS table_name, "
    "       c.relname AS object_name, "
    "       'INDEX' AS object_type, "
    "       'INVALID' AS status "
    "FROM pg_index i "
    "JOIN pg_class c ON c.oid = i.indexrelid "
    "JOIN pg_class t ON t.oid = i.indrelid "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE i.indisvalid = false "
    "ORDER BY n.nspname, t.relname, c.relname"
)

REDO_LOG_STATUS = (
    "SELECT name AS wal_file, "
    "       size AS size_bytes, "
    "       pg_size_pretty(size) AS size_pretty, "
    "       modification AS last_modified "
    "FROM pg_ls_waldir() "
    "ORDER BY modification DESC "
    "LIMIT 20"
)

ACTIVE_SESSIONS = (
    "SELECT pid, usename, datname, state, "
    "       client_addr, application_name, "
    "       wait_event_type, wait_event, "
    "       EXTRACT(EPOCH FROM (now() - query_start))::int AS seconds_running, "
    "       LEFT(query, 80) AS query_text "
    "FROM pg_stat_activity "
    "WHERE state = 'active' AND backend_type = 'client backend' "
    "ORDER BY query_start NULLS LAST"
)


# ===========================================================================
# Template registry: key -> (sql, keyword_groups)
# Keyword groups mirror templates_oracle.py for cross-engine routing parity.
# ===========================================================================

TEMPLATES: dict[str, tuple[str, list[list[str]]]] = {
    "tablespace_usage": (
        TABLESPACE_SQL,
        [
            ["tablespace", "tablespaces"],
            ["usage", "utilization", "space", "full", "capacity", "used", "free", "pct", "percent"],
        ],
    ),
    "top_sql_elapsed": (
        TOP_SQL_ELAPSED,
        [
            ["sql", "query", "queries", "statement"],
            ["top", "slow", "worst", "elapsed", "performance", "cpu", "long", "time", "expensive"],
        ],
    ),
    "blocking_sessions": (
        BLOCKING_SESSIONS,
        [
            ["block", "blocking", "blocked", "lock", "locked", "deadlock", "contention", "waiting"],
        ],
    ),
    "invalid_objects": (
        INVALID_OBJECTS,
        [
            ["invalid", "broken", "compile", "compilation"],
            ["object", "objects", "index", "indexes", "procedure", "view", "function", "trigger"],
        ],
    ),
    "redo_log_status": (
        REDO_LOG_STATUS,
        [
            # "wal" added — Postgres users say WAL where Oracle users say redo.
            ["redo", "archivelog", "archive", "log group", "redo log", "wal"],
        ],
    ),
    "active_sessions": (
        ACTIVE_SESSIONS,
        [
            ["active", "current", "connected", "who is", "who are"],
            ["session", "sessions", "user", "users", "connection", "connections"],
        ],
    ),
    # rman_backup_history intentionally omitted — no RMAN in Postgres.
}

# Curated Q->SQL pairs for Tier-2 Vanna training (Postgres store).
CURATED_QUERIES: list[dict[str, str]] = [
    {"question": "Show me tablespace utilization",                 "sql": TABLESPACE_SQL},
    {"question": "How much space is each tablespace using?",       "sql": TABLESPACE_SQL},
    {"question": "Show top 10 SQL by elapsed time",                "sql": TOP_SQL_ELAPSED},
    {"question": "What are the slowest queries?",                  "sql": TOP_SQL_ELAPSED},
    {"question": "Show active blocking sessions right now",        "sql": BLOCKING_SESSIONS},
    {"question": "Which sessions are blocked and by whom?",        "sql": BLOCKING_SESSIONS},
    {"question": "Are there any invalid indexes?",                 "sql": INVALID_OBJECTS},
    {"question": "Show WAL / redo log status",                     "sql": REDO_LOG_STATUS},
    {"question": "Who is connected to the database right now?",    "sql": ACTIVE_SESSIONS},
    {"question": "Show all active user sessions",                  "sql": ACTIVE_SESSIONS},
]


if __name__ == "__main__":
    print(f"Postgres templates: {len(TEMPLATES)} | curated Q->SQL pairs: {len(CURATED_QUERIES)}")
    for k in TEMPLATES:
        print(f"  - {k}")
