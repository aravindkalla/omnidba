"""
OmniDBA — FastAPI REST backend for the mobile application.

Runs on port 8000 alongside the existing Streamlit UI (port 8501).
All LangGraph logic in orchestrator.py / diagnostic_agent.py / rman_agent.py
is completely unchanged — this is a REST adapter over the existing pipeline.

Mobile-only capabilities added here:
    • JWT authentication + biometric-friendly token refresh
    • Expo push token registration and HITL push delivery
    • GET /health-report — 7 Oracle metrics, chart-ready JSON, one call
    • POST /schedule    — recurring RMAN backup jobs (always HITL-gated)
    • Admin endpoints   — user management

Start the API (alongside Streamlit):
    cd /opt/oracle-dba-agent
    source .venv/bin/activate
    uvicorn api:app --host 0.0.0.0 --port 8000
"""

import asyncio
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from langgraph.types import Command
from pydantic import BaseModel

from auth import (
    User,
    add_user,
    all_users,
    authenticate_user,
    create_access_token,
    create_refresh_token,
    get_user,
    set_active,
    update_password,
    verify_token,
)
from push import register_token, send_hitl_push, unregister_token
from scheduler_bg import (
    ScheduledJob,
    add_job,
    delete_job,
    get_job,
    list_jobs,
    shutdown_scheduler,
    start_scheduler,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── LangGraph app (initialises Vanna + Oracle connection on import) ───────────
from orchestrator import app as lg_app  # noqa: E402

# Thread pool: runs blocking LangGraph / Oracle calls without stalling the
# FastAPI async event loop. max_workers=4 → up to 4 concurrent DBA queries.
_executor = ThreadPoolExecutor(max_workers=4)

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title       = "OmniDBA API",
    description = "REST backend for the OmniDBA mobile application",
    version     = "1.0.0",
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins  = ["*"],   # tighten to your domain in production
    allow_methods  = ["*"],
    allow_headers  = ["*"],
)

_bearer = HTTPBearer()


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def _startup() -> None:
    start_scheduler()
    logger.info("OmniDBA API started on port 8000")


@app.on_event("shutdown")
async def _shutdown() -> None:
    shutdown_scheduler()
    _executor.shutdown(wait=False)


# ── Auth dependency ───────────────────────────────────────────────────────────

async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> User:
    try:
        email = verify_token(creds.credentials, expected_type="access")
    except JWTError as exc:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail      = f"Invalid or expired token: {exc}",
            headers     = {"WWW-Authenticate": "Bearer"},
        )
    user = get_user(email)
    if not user or not user.is_active:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail      = "User not found or account disabled",
        )
    return user


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


# =============================================================================
#  REQUEST / RESPONSE MODELS
# =============================================================================

class LoginRequest(BaseModel):
    email:    str
    password: str


class TokenResponse(BaseModel):
    access_token:         str
    refresh_token:        str
    token_type:           str = "bearer"
    role:                 str
    must_change_password: bool


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    new_password: str


class PushRegisterRequest(BaseModel):
    expo_token: str


class QueryRequest(BaseModel):
    text:      str
    thread_id: Optional[str] = None


class QueryResponse(BaseModel):
    thread_id:    str
    intent:       Optional[str]
    final_result: Optional[str]
    sql:          Optional[str]
    columns:      list
    rows:         list
    query_error:  bool
    hitl_pending: bool
    hitl_payload: Optional[dict]


class ApproveRequest(BaseModel):
    thread_id: str
    response:  str   # "approve" | "reject" | free-text revision


class CreateScheduleRequest(BaseModel):
    name:         str   # human label shown in mobile app
    cron_expr:    str   # "0 2 * * *" → 2 AM UTC daily
    backup_query: str   # NL text identical to what the DBA would type in chat


class CreateUserRequest(BaseModel):
    email:    str
    password: str
    role:     str = "dba"


# =============================================================================
#  AUTH ENDPOINTS
# =============================================================================

@app.post("/auth/login", response_model=TokenResponse, tags=["auth"])
async def login(body: LoginRequest):
    """
    Validate credentials and issue JWT access + refresh tokens.

    The mobile app stores both tokens in expo-secure-store.
    On subsequent launches, biometric auth unlocks the stored access token
    so the user never has to type their password again.
    """
    user = authenticate_user(body.email, body.password)
    if not user:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail      = "Invalid email or password",
        )
    return TokenResponse(
        access_token         = create_access_token(user.email),
        refresh_token        = create_refresh_token(user.email),
        role                 = user.role,
        must_change_password = user.must_change_password,
    )


@app.post("/auth/refresh", response_model=TokenResponse, tags=["auth"])
async def refresh_token(body: RefreshRequest):
    """
    Exchange a valid refresh token for new access + refresh tokens.
    Called silently by the mobile app when the access token expires.
    """
    try:
        email = verify_token(body.refresh_token, expected_type="refresh")
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    user = get_user(email)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return TokenResponse(
        access_token         = create_access_token(email),
        refresh_token        = create_refresh_token(email),
        role                 = user.role,
        must_change_password = user.must_change_password,
    )


@app.post("/auth/change-password", tags=["auth"])
async def change_password(
    body:         ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
):
    """Let any authenticated user change their own password."""
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    update_password(current_user.email, body.new_password)
    return {"message": "Password updated successfully"}


# =============================================================================
#  PUSH NOTIFICATION ENDPOINTS
# =============================================================================

@app.post("/push/register", tags=["push"])
async def push_register(
    body:         PushRegisterRequest,
    current_user: User = Depends(get_current_user),
):
    """Register or update the Expo push token for the authenticated user's device."""
    register_token(current_user.email, body.expo_token)
    return {"message": "Push token registered"}


@app.delete("/push/register", tags=["push"])
async def push_unregister(current_user: User = Depends(get_current_user)):
    """Remove push token on logout or notification opt-out."""
    unregister_token(current_user.email)
    return {"message": "Push token removed"}


# =============================================================================
#  QUERY ENDPOINT — natural language → LangGraph → result
# =============================================================================

@app.post("/query", response_model=QueryResponse, tags=["query"])
async def query(
    body:         QueryRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Submit a natural-language Oracle DBA query.

    The LangGraph orchestrator routes to diagnostic or backup agent.
    If a backup request triggers the HITL interrupt:
      - hitl_pending=true is returned
      - A push notification is broadcast to all registered DBA devices
      - The mobile app deep-links to the HITL approval screen on tap
    """
    thread_id = body.thread_id or str(uuid.uuid4())
    config    = {"configurable": {"thread_id": thread_id}}
    loop      = asyncio.get_event_loop()

    try:
        result = await loop.run_in_executor(
            _executor,
            lambda: lg_app.invoke({"query": body.text}, config=config),
        )
    except Exception as exc:
        logger.error("LangGraph invoke failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}")

    snapshot   = lg_app.get_state(config)
    interrupts = [i for t in snapshot.tasks for i in t.interrupts]
    hitl_pending = len(interrupts) > 0
    hitl_payload = interrupts[0].value if interrupts else None

    if hitl_pending and hitl_payload:
        await send_hitl_push(
            thread_id = thread_id,
            payload   = hitl_payload,
            requester = current_user.email,
        )

    return QueryResponse(
        thread_id    = thread_id,
        intent       = result.get("user_intent"),
        final_result = result.get("final_result"),
        sql          = result.get("generated_sql"),
        columns      = result.get("query_columns", []),
        rows         = result.get("query_rows",    []),
        query_error  = result.get("query_error",   False),
        hitl_pending = hitl_pending,
        hitl_payload = hitl_payload,
    )


@app.post("/approve", tags=["query"])
async def approve(
    body:         ApproveRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Resume a paused LangGraph thread with the DBA's HITL decision.

    body.response values:
      "approve"  — execute the RMAN script
      "reject"   — abort (converted to the abort message backup_node expects)
      <any text> — revision feedback; backup_node regenerates the script and
                   issues a second interrupt for the revised plan
    """
    config   = {"configurable": {"thread_id": body.thread_id}}
    response = body.response.strip()

    if response.lower() == "reject":
        response = "Operation rejected by database administrator."

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            _executor,
            lambda: lg_app.invoke(Command(resume=response), config=config),
        )
    except Exception as exc:
        logger.error("HITL resume failed for thread %s: %s", body.thread_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Resume error: {exc}")

    snapshot   = lg_app.get_state(config)
    interrupts = [i for t in snapshot.tasks for i in t.interrupts]
    hitl_pending = len(interrupts) > 0
    hitl_payload = interrupts[0].value if interrupts else None

    # Second interrupt — revised script needs another approval
    if hitl_pending and hitl_payload:
        await send_hitl_push(
            thread_id = body.thread_id,
            payload   = hitl_payload,
            requester = current_user.email,
        )

    return {
        "thread_id":    body.thread_id,
        "final_result": result.get("final_result"),
        "hitl_pending": hitl_pending,
        "hitl_payload": hitl_payload,
        "approved_by":  current_user.email,
    }


# =============================================================================
#  HEALTH REPORT — 7 Oracle metrics for the mobile dashboard
# =============================================================================

# Custom SQL not covered by diagnostic_agent templates
_BUFFER_CACHE_SQL = """
SELECT ROUND(
    (1 - (
        (SELECT value FROM v$sysstat WHERE name = 'physical reads') /
        NULLIF(
            (SELECT value FROM v$sysstat WHERE name = 'db block gets') +
            (SELECT value FROM v$sysstat WHERE name = 'consistent gets'),
            0
        )
    )) * 100, 2
) AS hit_ratio_pct
FROM DUAL
"""

# Redo log switches per hour for the last 24 hours (drives line chart on mobile)
_REDO_SWITCHES_SQL = """
SELECT TO_CHAR(FIRST_TIME, 'YYYY-MM-DD HH24') AS hour_bucket,
       COUNT(*)                               AS switch_count
FROM   v$log_history
WHERE  FIRST_TIME >= SYSDATE - 1
GROUP BY TO_CHAR(FIRST_TIME, 'YYYY-MM-DD HH24')
ORDER BY 1
"""

# Maps metric key → NL query sent to diagnostic_agent (None = custom SQL path)
_HEALTH_METRICS: dict[str, Optional[str]] = {
    "tablespace_usage":      "show tablespace usage",
    "active_sessions":       "show active user sessions",
    "blocking_sessions":     "show blocking sessions",
    "top_sql":               "top SQL by elapsed time",
    "invalid_objects":       "show invalid objects",
    "buffer_cache_hit_ratio": None,   # custom SQL
    "redo_switches_24h":     None,    # custom SQL
}

_CUSTOM_SQL: dict[str, str] = {
    "buffer_cache_hit_ratio": _BUFFER_CACHE_SQL,
    "redo_switches_24h":      _REDO_SWITCHES_SQL,
}


def _status_for_metric(key: str, columns: list, rows: list) -> str:
    """Compute ok / warning / critical based on metric-specific thresholds."""
    if not rows:
        return "ok"
    try:
        if key == "tablespace_usage":
            idx = next(i for i, c in enumerate(columns) if "PCT" in c.upper())
            max_pct = max(float(r[idx] or 0) for r in rows)
            return "critical" if max_pct >= 90 else "warning" if max_pct >= 80 else "ok"

        if key == "blocking_sessions":
            return "critical" if rows else "ok"

        if key == "active_sessions":
            n = len(rows)
            return "critical" if n >= 100 else "warning" if n >= 50 else "ok"

        if key == "buffer_cache_hit_ratio":
            ratio = float(rows[0][0] or 0)
            return "critical" if ratio < 80 else "warning" if ratio < 90 else "ok"

        if key == "invalid_objects":
            return "warning" if rows else "ok"

    except (StopIteration, IndexError, ValueError, TypeError):
        pass
    return "ok"


def _fetch_metric(key: str, query_text: Optional[str]) -> dict:
    """Execute one health metric query. Runs in the thread pool executor."""
    from diagnostic_agent import connection, run_diagnostic_query  # noqa: PLC0415
    import oracledb  # noqa: PLC0415

    try:
        if key in _CUSTOM_SQL:
            with connection.cursor() as cur:
                cur.execute(_CUSTOM_SQL[key])
                columns = [c[0] for c in cur.description]
                raw     = cur.fetchall()
                rows    = [
                    [v.read() if isinstance(v, oracledb.LOB) else v for v in row]
                    for row in raw
                ]
        else:
            _sql, columns, data = run_diagnostic_query(query_text)
            rows = [list(r) for r in data]

        return {
            "columns": columns,
            "rows":    rows,
            "status":  _status_for_metric(key, columns, rows),
            "error":   None,
        }
    except Exception as exc:
        logger.error("Health metric '%s' failed: %s", key, exc)
        return {"columns": [], "rows": [], "status": "unknown", "error": str(exc)}


@app.get("/health-report", tags=["diagnostics"])
async def health_report(current_user: User = Depends(get_current_user)):
    """
    Run all 7 Oracle health metrics and return chart-ready JSON in one call.

    Each metric includes: columns, rows, status (ok/warning/critical), error.
    Typical response time: 2–5 seconds depending on DB load.

    Mobile app uses this to populate the Health Dashboard tab with:
      tablespace_usage      → horizontal bar chart (PCT_USED per tablespace)
      active_sessions       → number card (color-coded by threshold)
      blocking_sessions     → red alert card when count > 0
      top_sql               → horizontal bar chart (elapsed time)
      invalid_objects       → number card
      buffer_cache_hit_ratio → progress ring (green ≥ 90%, amber ≥ 80%, red < 80%)
      redo_switches_24h     → line chart by hour
    """
    loop    = asyncio.get_event_loop()
    metrics: dict[str, dict] = {}

    for key, query_text in _HEALTH_METRICS.items():
        metrics[key] = await loop.run_in_executor(
            _executor,
            lambda k=key, q=query_text: _fetch_metric(k, q),
        )

    all_statuses = [m["status"] for m in metrics.values()]
    overall = (
        "critical" if "critical" in all_statuses else
        "warning"  if "warning"  in all_statuses else
        "ok"
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall":      overall,
        "metrics":      metrics,
    }


# =============================================================================
#  SCHEDULER ENDPOINTS
# =============================================================================

@app.post("/schedule", tags=["scheduler"])
async def create_schedule(
    body:         CreateScheduleRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Create a recurring scheduled backup.

    When the cron fires, LangGraph generates the RMAN script and sends an
    HITL push to all DBA devices — the DBA still must approve before RMAN runs.
    Cron times are UTC.  Example: "0 2 * * *" = 2 AM UTC every day.
    """
    try:
        job = add_job(
            name         = body.name,
            cron_expr    = body.cron_expr,
            backup_query = body.backup_query,
            created_by   = current_user.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _serialize_job(job)


@app.get("/schedules", tags=["scheduler"])
async def get_schedules(current_user: User = Depends(get_current_user)):
    """List all scheduled backup jobs."""
    return [_serialize_job(j) for j in list_jobs()]


@app.delete("/schedule/{job_id}", tags=["scheduler"])
async def cancel_schedule(
    job_id:       str,
    current_user: User = Depends(get_current_user),
):
    """Cancel a scheduled backup. Only the creator or an admin can delete."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.created_by != current_user.email and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="You can only cancel your own jobs")
    delete_job(job_id)
    return {"message": f"Job '{job.name}' cancelled"}


def _serialize_job(job: ScheduledJob) -> dict:
    return {
        "job_id":       job.job_id,
        "name":         job.name,
        "cron_expr":    job.cron_expr,
        "backup_query": job.backup_query,
        "created_by":   job.created_by,
        "created_at":   job.created_at,
        "enabled":      job.enabled,
        "last_fired":   job.last_fired,
    }


# =============================================================================
#  ADMIN ENDPOINTS
# =============================================================================

@app.get("/admin/users", tags=["admin"])
async def admin_list_users(admin: User = Depends(require_admin)):
    """List all users. Passwords are never returned."""
    return [
        {
            "email":                u.email,
            "role":                 u.role,
            "is_active":            u.is_active,
            "must_change_password": u.must_change_password,
        }
        for u in all_users()
    ]


@app.post("/admin/users", tags=["admin"])
async def admin_create_user(
    body:  CreateUserRequest,
    admin: User = Depends(require_admin),
):
    """Create a new user account (admin only)."""
    try:
        user = add_user(body.email, body.password, role=body.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "email":                user.email,
        "role":                 user.role,
        "must_change_password": user.must_change_password,
    }


@app.patch("/admin/users/{email}/deactivate", tags=["admin"])
async def admin_deactivate_user(email: str, admin: User = Depends(require_admin)):
    """Disable a user account."""
    if email == admin.email:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")
    if not set_active(email, False):
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": f"{email} deactivated"}


@app.patch("/admin/users/{email}/activate", tags=["admin"])
async def admin_activate_user(email: str, admin: User = Depends(require_admin)):
    """Re-enable a user account."""
    if not set_active(email, True):
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": f"{email} activated"}


# =============================================================================
#  STATUS ENDPOINT — no auth required (used by nginx health checks)
# =============================================================================

@app.get("/status", tags=["health"])
async def api_status():
    """Health check for the API, Oracle, and Ollama. No auth required."""
    import httpx as _httpx  # noqa: PLC0415

    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    oracle_ok   = False
    ollama_ok   = False

    try:
        from diagnostic_agent import connection  # noqa: PLC0415
        with connection.cursor() as cur:
            cur.execute("SELECT 1 FROM DUAL")
        oracle_ok = True
    except Exception as exc:
        logger.warning("Oracle health check failed: %s", exc)

    try:
        async with _httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{ollama_host}/api/tags")
            ollama_ok = r.status_code == 200
    except Exception:
        pass

    return {
        "api":    "ok",
        "oracle": "ok" if oracle_ok else "unreachable",
        "ollama": "ok" if ollama_ok else "unreachable",
    }
