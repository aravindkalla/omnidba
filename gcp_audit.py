"""
GCP governance sinks for OmniDBA v3 — BigQuery audit trail + Cloud Tasks HITL queue.

Both sinks are BEST-EFFORT and NON-BLOCKING. A failure (no creds, API disabled,
fully air-gapped Ollama demo, network down) must NEVER break a user turn: the
first failure disables that sink for the process and logs one warning; later
calls are cheap no-ops. Disable explicitly with AUDIT_SINK=off / HITL_SINK=off
(e.g. the air-gapped demo, where there is no GCP at all).

Provisioned by docs/BUILD_FROM_SCRATCH.md §8:
  - BigQuery  : {GCP_PROJECT}.{BQ_AUDIT_DATASET}.turns  (default omnidba_audit.turns)
  - CloudTasks: queue {HITL_QUEUE} in {GCP_REGION}       (default omnidba-hitl / asia-south1)

The BigQuery write runs on a small daemon thread pool so it never adds latency to
the response path (the <3s p95 budget). Long-lived servers fire-and-forget; tests
and graceful shutdown call flush().
"""

import os
import sys
import json
import atexit
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

_PROJECT    = os.getenv("GCP_PROJECT")
_REGION     = os.getenv("GCP_REGION", "asia-south1")
_DATASET    = os.getenv("BQ_AUDIT_DATASET", "omnidba_audit")
_TABLE      = os.getenv("BQ_AUDIT_TABLE", "turns")
_HITL_QUEUE = os.getenv("HITL_QUEUE", "omnidba-hitl")
# Cloud Tasks requires every task to have a delivery target. For the demo this is
# a placeholder; a real deployment points it at an operator/approval webhook.
_HITL_URL   = os.getenv("HITL_CALLBACK_URL", "https://omnidba.invalid/hitl/approval")

# A sink is on only when a project is configured AND not explicitly turned off.
_AUDIT_OFF = os.getenv("AUDIT_SINK", "auto").lower() == "off" or not _PROJECT
_HITL_OFF  = os.getenv("HITL_SINK",  "auto").lower() == "off" or not _PROJECT

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gcp-audit")

# Lazy client singletons + disable-on-first-failure latches.
_bq = None
_bq_dead = False
_tasks = None
_tasks_dead = False


def _warn(msg: str) -> None:
    print(f"[gcp_audit] {msg}", file=sys.stderr)


def _bq_client():
    global _bq
    if _bq is None:
        from google.cloud import bigquery
        _bq = bigquery.Client(project=_PROJECT)
    return _bq


def _write_turn(row: dict) -> None:
    """Runs on the thread pool. Streams one row into BigQuery."""
    global _bq_dead
    try:
        table_id = f"{_PROJECT}.{_DATASET}.{_TABLE}"
        errors = _bq_client().insert_rows_json(table_id, [row])
        if errors:
            _warn(f"BigQuery insert errors: {errors}")
    except Exception as e:  # noqa: BLE001 — best-effort, never propagate
        if not _bq_dead:
            _bq_dead = True
            _warn(f"audit sink disabled after error: {e}")


def log_turn(*, query, engine, provider, intent, generated_sql,
             row_count, latency_ms, error, error_msg="", user="agent") -> None:
    """Record one NL→SQL turn to the BigQuery audit trail (best-effort, async)."""
    if _AUDIT_OFF or _bq_dead:
        return
    row = {
        "ts":            datetime.now(timezone.utc).isoformat(),
        "user":          user,
        "engine":        engine,
        "provider":      provider,
        "intent":        intent,
        "query":         query,
        "generated_sql": generated_sql or "",
        "row_count":     int(row_count),
        "latency_ms":    int(latency_ms),
        "error":         bool(error),
        "error_msg":     (error_msg or "")[:2000],
    }
    try:
        _executor.submit(_write_turn, row)
    except RuntimeError:
        # Executor already shut down (process exiting) — write inline as a fallback.
        _write_turn(row)


def enqueue_hitl_approval(*, thread_id, rman_script, backup_params, revision=False):
    """Enqueue a pending RMAN approval onto the Cloud Tasks HITL queue.

    Best-effort: returns the created task name, or None if the sink is disabled
    or the call fails. Never raises — an approval must still work air-gapped."""
    global _tasks, _tasks_dead
    if _HITL_OFF or _tasks_dead:
        return None
    try:
        from google.cloud import tasks_v2
        if _tasks is None:
            _tasks = tasks_v2.CloudTasksClient()
        parent = _tasks.queue_path(_PROJECT, _REGION, _HITL_QUEUE)
        body = json.dumps({
            "thread_id":     thread_id,
            "revision":      revision,
            "backup_params": backup_params,
            "rman_script":   rman_script,
            "enqueued_at":   datetime.now(timezone.utc).isoformat(),
        }).encode()
        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url":         _HITL_URL,
                "headers":     {"Content-Type": "application/json"},
                "body":        body,
            }
        }
        created = _tasks.create_task(request={"parent": parent, "task": task})
        return created.name
    except Exception as e:  # noqa: BLE001 — best-effort, never propagate
        if not _tasks_dead:
            _tasks_dead = True
            _warn(f"HITL queue disabled after error: {e}")
        return None


def flush(timeout: float = 10) -> None:
    """Block until queued audit writes finish (tests / graceful shutdown)."""
    _executor.shutdown(wait=True)


def status() -> dict:
    """Introspection for /health banners."""
    return {
        "audit_sink": "off" if _AUDIT_OFF else ("dead" if _bq_dead else f"{_DATASET}.{_TABLE}"),
        "hitl_queue": "off" if _HITL_OFF else ("dead" if _tasks_dead else _HITL_QUEUE),
        "project":    _PROJECT,
    }


atexit.register(lambda: _executor.shutdown(wait=True))


if __name__ == "__main__":
    print("gcp_audit status:", status())
