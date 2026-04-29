"""
Expo Push Notification integration for OmniDBA mobile API.

Each mobile device registers its Expo push token via POST /push/register.
Tokens are stored per-user in push_tokens.json.

When a LangGraph HITL interrupt fires, send_hitl_push() broadcasts to every
registered DBA device so any team member can approve or reject the backup.

Push is best-effort — a failure here never blocks the HITL workflow itself.
The mobile app can always poll GET /query/{thread_id}/status as a fallback.

Expo Push API docs: https://docs.expo.dev/push-notifications/sending-notifications/
"""

import json
import logging
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

TOKENS_FILE   = Path(__file__).parent / "push_tokens.json"
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


# ── Token store ───────────────────────────────────────────────────────────────

def _load_tokens() -> dict[str, str]:
    """Returns {email: expo_push_token}."""
    if not TOKENS_FILE.exists():
        return {}
    with TOKENS_FILE.open() as fh:
        return json.load(fh)


def _save_tokens(tokens: dict[str, str]) -> None:
    with TOKENS_FILE.open("w") as fh:
        json.dump(tokens, fh, indent=2)


def register_token(email: str, expo_token: str) -> None:
    """Persist or update the Expo push token for a user's device."""
    tokens = _load_tokens()
    tokens[email] = expo_token
    _save_tokens(tokens)
    logger.info("Push token registered for %s", email)


def unregister_token(email: str) -> None:
    """Remove push token on logout or notification opt-out."""
    tokens = _load_tokens()
    if email in tokens:
        del tokens[email]
        _save_tokens(tokens)
        logger.info("Push token removed for %s", email)


# ── Push delivery ─────────────────────────────────────────────────────────────

async def send_hitl_push(
    thread_id: str,
    payload: dict,
    requester: str,
    schedule_name: Optional[str] = None,
) -> None:
    """
    Broadcast an HITL approval request to every registered DBA device.

    payload  : the interrupt value from LangGraph — contains rman_script + backup_params
    requester: email of the user who triggered the backup request
    schedule_name: set when triggered by a scheduled job (not an ad-hoc request)
    """
    tokens = _load_tokens()
    if not tokens:
        logger.warning("No push tokens registered — HITL push not sent (thread_id=%s)", thread_id)
        return

    backup_type = payload.get("backup_params", {}).get("backup_type", "backup")
    if schedule_name:
        title = f"Scheduled '{schedule_name}' Needs Approval"
        body  = f"{backup_type.capitalize()} backup scheduled by {requester} — tap to review"
    else:
        title = "DBA Approval Required"
        body  = f"{requester} requested a {backup_type} backup — tap to review RMAN script"

    messages = [
        {
            "to":      token,
            "title":   title,
            "body":    body,
            "data":    {"screen": "hitl-approval", "thread_id": thread_id},
            "sound":   "default",
            "priority": "high",
        }
        for token in tokens.values()
    ]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(EXPO_PUSH_URL, json=messages)
            resp.raise_for_status()
            logger.info(
                "HITL push sent to %d device(s) for thread_id=%s",
                len(messages), thread_id,
            )
    except Exception as exc:
        # Non-fatal — HITL still completes via the mobile app's polling fallback
        logger.error("Push notification failed (non-fatal): %s", exc)


async def send_generic_push(
    emails: list[str],
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> None:
    """Send a targeted push notification to specific users by email."""
    tokens = _load_tokens()
    messages = [
        {
            "to":    tokens[e],
            "title": title,
            "body":  body,
            "data":  data or {},
            "sound": "default",
        }
        for e in emails if e in tokens
    ]
    if not messages:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(EXPO_PUSH_URL, json=messages)
            resp.raise_for_status()
            logger.info("Generic push sent to %d device(s)", len(messages))
    except Exception as exc:
        logger.error("Generic push failed: %s", exc)
