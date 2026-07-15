"""
Oracle Tier-1 diagnostic SQL templates.

Extracted verbatim from v1 diagnostic_agent.py `_TEMPLATES` so the v3 engine-
agnostic pipeline (db_adapter.OracleAdapter.templates()) can load them.

Each entry: key -> (sql, keyword_groups)
  keyword_groups : list of lists — the query scores +1 per inner list that has
                   at least one word present. Highest score (min 1) wins.

Validated against Oracle 26ai Free. Only real column names are used, so these
run without the LLM (zero hallucination risk) — Tier 1 of the 3-tier NL2SQL.
"""

# ===========================================================================
# Canonical SQL
# ===========================================================================

TABLESPACE_SQL = (
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

TOP_SQL_ELAPSED = (
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

BLOCKING_SESSIONS = (
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

INVALID_OBJECTS = (
    "SELECT owner, object_name, object_type, last_ddl_time, status "
    "FROM dba_objects "
    "WHERE status = 'INVALID' "
    "ORDER BY owner, object_type, object_name"
)

REDO_LOG_STATUS = (
    "SELECT l.group#, l.members, "
    "       ROUND(l.bytes / 1048576, 1) AS size_mb, "
    "       l.status, l.archived, lf.member AS log_file "
    "FROM v$log l "
    "JOIN v$logfile lf ON l.group# = lf.group# "
    "ORDER BY l.group#"
)

ACTIVE_SESSIONS = (
    "SELECT sid, serial#, username, status, machine, program, "
    "       wait_class, event, seconds_in_wait, sql_id "
    "FROM v$session "
    "WHERE type = 'USER' AND username IS NOT NULL "
    "ORDER BY seconds_in_wait DESC NULLS LAST"
)

RMAN_BACKUP_HISTORY = (
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


# ===========================================================================
# Template registry: key -> (sql, keyword_groups)
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
            ["object", "objects", "procedure", "package", "view", "function", "trigger"],
        ],
    ),
    "redo_log_status": (
        REDO_LOG_STATUS,
        [
            ["redo", "archivelog", "archive", "log group", "redo log"],
        ],
    ),
    "active_sessions": (
        ACTIVE_SESSIONS,
        [
            ["active", "current", "connected", "who is", "who are"],
            ["session", "sessions", "user", "users", "connection", "connections"],
        ],
    ),
    "rman_backup_history": (
        RMAN_BACKUP_HISTORY,
        [
            ["list", "show", "display", "history", "recent", "last", "previous", "status"],
            ["rman", "backup", "backups", "backup job", "backup jobs", "backup history"],
        ],
    ),
}

# Curated Q->SQL pairs for Tier-2 Vanna training (aligned to the templates above).
# Lifted from v1 diagnostic_agent.train_on_oracle_schema(); kept here so the
# training set travels with the templates it mirrors.
CURATED_QUERIES: list[dict[str, str]] = [
    {"question": "What is the current tablespace usage percentage?", "sql": TABLESPACE_SQL},
    {"question": "Show me tablespace utilization",                   "sql": TABLESPACE_SQL},
    {"question": "How full are my tablespaces?",                     "sql": TABLESPACE_SQL},
    {"question": "Which tablespaces are almost full or critical?",   "sql": TABLESPACE_SQL},
    {"question": "Show tablespace space usage in GB",                "sql": TABLESPACE_SQL},
    {"question": "Show top 10 SQL by elapsed time in the last hour", "sql": TOP_SQL_ELAPSED},
    {"question": "What are the slowest queries right now?",          "sql": TOP_SQL_ELAPSED},
    {"question": "Show active blocking sessions right now",          "sql": BLOCKING_SESSIONS},
    {"question": "Are there any lock contentions or deadlocks?",     "sql": BLOCKING_SESSIONS},
    {"question": "Are there any invalid database objects?",          "sql": INVALID_OBJECTS},
    {"question": "List all invalid procedures and packages",         "sql": INVALID_OBJECTS},
    {"question": "Show redo log group status and archiving",         "sql": REDO_LOG_STATUS},
    {"question": "Who is connected to the database right now?",      "sql": ACTIVE_SESSIONS},
    {"question": "Show all active user sessions",                    "sql": ACTIVE_SESSIONS},
]


if __name__ == "__main__":
    print(f"Oracle templates: {len(TEMPLATES)} | curated Q->SQL pairs: {len(CURATED_QUERIES)}")
    for k in TEMPLATES:
        print(f"  - {k}")
