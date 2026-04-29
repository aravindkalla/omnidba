"""
APScheduler background job runner for OmniDBA scheduled backups.

IMPORTANT: Scheduled jobs do NOT auto-execute RMAN.
When a cron fires, the scheduler generates the RMAN script through the normal
LangGraph backup pipeline, then sends an HITL push notification to all DBA
devices for approval — exactly like an on-demand chat request.

No unattended RMAN execution ever happens.

Job persistence: jobs.json — jobs survive API restarts and are re-registered
with APScheduler on startup.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

JOBS_FILE = Path(__file__).parent / "jobs.json"


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class ScheduledJob:
    job_id:       str
    name:         str           # human-readable label shown in the mobile app
    cron_expr:    str           # e.g. "0 2 * * *"  (2 AM every day, UTC)
    backup_query: str           # NL query — same as what DBA would type in chat
    created_by:   str           # email of creator
    created_at:   str           # ISO-8601 timestamp
    enabled:      bool
    last_fired:   Optional[str] = None   # ISO-8601 of last trigger (None if never run)


# ── Job persistence ───────────────────────────────────────────────────────────

def _load_jobs() -> dict[str, dict]:
    if not JOBS_FILE.exists():
        return {}
    with JOBS_FILE.open() as fh:
        return json.load(fh)


def _save_jobs(jobs: dict[str, dict]) -> None:
    with JOBS_FILE.open("w") as fh:
        json.dump(jobs, fh, indent=2)


def list_jobs() -> list[ScheduledJob]:
    return [ScheduledJob(**v) for v in _load_jobs().values()]


def get_job(job_id: str) -> Optional[ScheduledJob]:
    data = _load_jobs().get(job_id)
    return ScheduledJob(**data) if data else None


def _persist_job(job: ScheduledJob) -> None:
    jobs = _load_jobs()
    jobs[job.job_id] = asdict(job)
    _save_jobs(jobs)


def _remove_job_from_disk(job_id: str) -> bool:
    jobs = _load_jobs()
    if job_id not in jobs:
        return False
    del jobs[job_id]
    _save_jobs(jobs)
    return True


def update_last_fired(job_id: str) -> None:
    jobs = _load_jobs()
    if job_id in jobs:
        jobs[job_id]["last_fired"] = datetime.now(timezone.utc).isoformat()
        _save_jobs(jobs)


# ── Scheduler singleton ───────────────────────────────────────────────────────

scheduler = AsyncIOScheduler(timezone="UTC")


def start_scheduler() -> None:
    """Start APScheduler and re-register all persisted enabled jobs."""
    scheduler.start()
    restored = 0
    for job in list_jobs():
        if job.enabled:
            _register_apscheduler(job)
            restored += 1
    logger.info("Scheduler started. Re-registered %d job(s) from jobs.json.", restored)


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def add_job(name: str, cron_expr: str, backup_query: str, created_by: str) -> ScheduledJob:
    """Create and persist a new scheduled backup job."""
    _validate_cron(cron_expr)
    job = ScheduledJob(
        job_id       = str(uuid.uuid4()),
        name         = name,
        cron_expr    = cron_expr,
        backup_query = backup_query,
        created_by   = created_by,
        created_at   = datetime.now(timezone.utc).isoformat(),
        enabled      = True,
    )
    _persist_job(job)
    _register_apscheduler(job)
    logger.info("Scheduled job created: %s  cron=%s  by=%s", name, cron_expr, created_by)
    return job


def delete_job(job_id: str) -> bool:
    """Remove a job from APScheduler and from disk."""
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    return _remove_job_from_disk(job_id)


def _validate_cron(expr: str) -> None:
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"Cron must have 5 space-separated fields "
            f"(minute hour day-of-month month day-of-week).  Got: '{expr}'"
        )
    # Let APScheduler validate the field values
    try:
        p = expr.split()
        CronTrigger(minute=p[0], hour=p[1], day=p[2], month=p[3], day_of_week=p[4])
    except Exception as exc:
        raise ValueError(f"Invalid cron expression '{expr}': {exc}") from exc


def _register_apscheduler(job: ScheduledJob) -> None:
    p = job.cron_expr.split()
    trigger = CronTrigger(
        minute=p[0], hour=p[1], day=p[2], month=p[3], day_of_week=p[4],
        timezone="UTC",
    )
    scheduler.add_job(
        run_scheduled_backup,
        trigger         = trigger,
        id              = job.job_id,
        kwargs          = {"job_id": job.job_id},
        replace_existing= True,
        misfire_grace_time = 600,   # fire up to 10 min late if server was down
    )


# ── Scheduled job executor ────────────────────────────────────────────────────

async def run_scheduled_backup(job_id: str) -> None:
    """
    Called by APScheduler when a cron fires.

    Runs the LangGraph backup pipeline to generate the RMAN script, then
    sends an HITL push notification to all DBAs for approval.
    RMAN is NOT executed here — that only happens after DBA approval.
    """
    # Local imports to avoid circular dependency at module load time
    from orchestrator import app as lg_app  # noqa: PLC0415
    from push import send_hitl_push          # noqa: PLC0415
    from concurrent.futures import ThreadPoolExecutor

    job = get_job(job_id)
    if not job or not job.enabled:
        logger.warning("Scheduled job %s not found or disabled — skipping", job_id)
        return

    logger.info("Scheduled backup firing: '%s'  cron=%s", job.name, job.cron_expr)
    update_last_fired(job_id)

    thread_id = str(uuid.uuid4())
    config    = {"configurable": {"thread_id": thread_id}}
    loop      = asyncio.get_event_loop()

    try:
        with ThreadPoolExecutor(max_workers=1) as exe:
            result = await loop.run_in_executor(
                exe,
                lambda: lg_app.invoke({"query": job.backup_query}, config=config),
            )
    except Exception as exc:
        logger.error("Scheduled backup LangGraph error for '%s': %s", job.name, exc, exc_info=True)
        return

    snapshot   = lg_app.get_state(config)
    interrupts = [i for t in snapshot.tasks for i in t.interrupts]

    if interrupts:
        await send_hitl_push(
            thread_id     = thread_id,
            payload       = interrupts[0].value,
            requester     = job.created_by,
            schedule_name = job.name,
        )
        logger.info(
            "Scheduled '%s' awaiting HITL approval. thread_id=%s",
            job.name, thread_id,
        )
    else:
        logger.warning(
            "Scheduled '%s' completed without HITL interrupt — unexpected for a backup request.",
            job.name,
        )
