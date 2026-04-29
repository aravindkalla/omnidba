"""
JWT authentication and user management for the OmniDBA mobile API.

User store   : users.json  — bcrypt-hashed passwords, no plaintext ever stored
Access token : 30-minute HS256 JWT  (sent with every API request)
Refresh token: 7-day HS256 JWT     (stored in SecureStore on device)

Environment variables:
    JWT_SECRET_KEY          MUST be set in .env before going live
    ACCESS_TOKEN_MINUTES    default 30
    REFRESH_TOKEN_DAYS      default 7
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

JWT_SECRET  = os.environ.get("JWT_SECRET_KEY", "CHANGE_THIS_BEFORE_PRODUCTION_LAUNCH")
JWT_ALGO    = "HS256"
ACCESS_TTL  = int(os.environ.get("ACCESS_TOKEN_MINUTES", "30"))
REFRESH_TTL = int(os.environ.get("REFRESH_TOKEN_DAYS", "7"))
USERS_FILE  = Path(__file__).parent / "users.json"


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class User:
    email:                str
    hashed_password:      str
    is_active:            bool
    role:                 str    # "admin" | "dba"
    must_change_password: bool


# ── User store ────────────────────────────────────────────────────────────────

def _load() -> dict[str, dict]:
    if not USERS_FILE.exists():
        return {}
    with USERS_FILE.open() as fh:
        return json.load(fh)


def _save(users: dict[str, dict]) -> None:
    with USERS_FILE.open("w") as fh:
        json.dump(users, fh, indent=2)


def get_user(email: str) -> Optional[User]:
    data = _load().get(email)
    return User(**data) if data else None


def all_users() -> list[User]:
    return [User(**v) for v in _load().values()]


# ── Password utilities ────────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def authenticate_user(email: str, password: str) -> Optional[User]:
    user = get_user(email)
    if not user or not user.is_active:
        return None
    return user if verify_password(password, user.hashed_password) else None


def update_password(email: str, new_password: str) -> bool:
    """Update password and clear the must_change_password flag."""
    users = _load()
    if email not in users:
        return False
    users[email]["hashed_password"]      = hash_password(new_password)
    users[email]["must_change_password"] = False
    _save(users)
    logger.info("Password updated for %s", email)
    return True


def set_active(email: str, active: bool) -> bool:
    """Enable or disable a user account."""
    users = _load()
    if email not in users:
        return False
    users[email]["is_active"] = active
    _save(users)
    logger.info("User %s active=%s", email, active)
    return True


def add_user(email: str, plain_password: str, role: str = "dba") -> User:
    """Create a new user (used by create_user.py CLI)."""
    users = _load()
    if email in users:
        raise ValueError(f"User '{email}' already exists")
    users[email] = {
        "email":                email,
        "hashed_password":      hash_password(plain_password),
        "is_active":            True,
        "role":                 role,
        "must_change_password": True,
    }
    _save(users)
    logger.info("Created user: %s  role=%s", email, role)
    return User(**users[email])


# ── Token creation / validation ───────────────────────────────────────────────

def create_access_token(email: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TTL)
    return jwt.encode(
        {"sub": email, "type": "access", "exp": exp},
        JWT_SECRET, algorithm=JWT_ALGO,
    )


def create_refresh_token(email: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=REFRESH_TTL)
    return jwt.encode(
        {"sub": email, "type": "refresh", "exp": exp},
        JWT_SECRET, algorithm=JWT_ALGO,
    )


def verify_token(token: str, expected_type: str = "access") -> str:
    """
    Validate a JWT and return the email (sub) claim.
    Raises JWTError on invalid / expired / wrong-type tokens.
    """
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    if payload.get("type") != expected_type:
        raise JWTError(f"Expected token type '{expected_type}', got '{payload.get('type')}'")
    email: str = payload.get("sub", "")
    if not email:
        raise JWTError("Missing 'sub' claim in token")
    return email
