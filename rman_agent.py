"""
RMAN Backup Agent — natural language → validated RMAN script → human-approved execution.

Pipeline
--------
  1. parse_backup_intent(query)  : NL → RMANParams via Ollama JSON mode
                                   keyword heuristic fallback if LLM fails
  2. generate_rman_script(params): RMANParams → deterministic, human-readable RMAN script
  3. execute_rman_backup(script) : ONLY called after explicit DBA approval in the
                                   LangGraph HITL interrupt workflow

Execution security
------------------
  Credentials are delivered to RMAN via stdin, never as command-line arguments,
  so the password never appears in the OS process table.

  Two auth modes (controlled by RMAN_OS_AUTH env var):
    RMAN_OS_AUTH=true   → rman target /          (OS authentication, simpler)
    RMAN_OS_AUTH=false  → credentials via stdin   (password auth, default)
"""

import dataclasses
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timedelta

from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_OLLAMA_HOST  = os.getenv("OLLAMA_HOST",  "http://localhost:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

_ORACLE_USER     = os.getenv("ORACLE_USER",     "sys")
_ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD", "password")
_ORACLE_DSN      = os.getenv("ORACLE_DSN",      "localhost/FREEPDB1")
# RMAN typically connects to the CDB root — override if it differs from the app DSN.
_RMAN_DSN        = os.getenv("RMAN_DSN",        _ORACLE_DSN)
# Set RMAN_OS_AUTH=true when running as the oracle OS user (no password needed).
_RMAN_OS_AUTH    = os.getenv("RMAN_OS_AUTH", "false").lower() == "true"
# When Oracle runs in a Docker container (our setup), `rman` is not on the host
# PATH — set RMAN_DOCKER_CONTAINER=oracle-26ai to exec rman inside that container
# (OS auth as the oracle user). Takes precedence over the host-path modes.
_RMAN_DOCKER     = os.getenv("RMAN_DOCKER_CONTAINER", "")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class RMANParams:
    backup_type:     str  = "full"     # full | incremental | archivelog
    level:           int  = 0          # incremental level: 0 = base, 1 = delta
    tag:             str  = ""         # auto-generated from type + timestamp if blank
    compress:        bool = True       # AS COMPRESSED BACKUPSET
    encrypt:         bool = False      # OB_MEDIA_ENCRYPT / SEND encryption directive
    channels:        int  = 2          # parallel RMAN channels (1–8)
    filesperset:     int  = 4          # backup pieces per set
    delay_hours:     int  = 0          # informational: intended hours before execution
    destination:     str  = ""         # FORMAT path prefix; empty = Oracle-configured default
    delete_input:    bool = True       # DELETE ALL INPUT archivelogs after backup
    delete_obsolete: bool = True       # DELETE NOPROMPT OBSOLETE after backup
    section_size:    str  = ""         # multi-section for large files e.g. "4G"


# ---------------------------------------------------------------------------
# LLM intent parser — primary path
# ---------------------------------------------------------------------------

_llm = ChatOllama(model=_OLLAMA_MODEL, base_url=_OLLAMA_HOST, format="json")

_INTENT_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """You are an Oracle DBA assistant. Extract RMAN backup parameters from the
user's request and return ONLY a valid JSON object with exactly these keys:

  "backup_type"    : "full" | "incremental" | "archivelog"
  "level"          : 0 or 1  (incremental base=0, delta=1; 0 for all other types)
  "tag"            : short label, uppercase + underscores, no spaces (empty string if not given)
  "compress"       : true | false
  "encrypt"        : true | false
  "channels"       : integer 1-8 (parallel channels)
  "filesperset"    : integer 1-16 (backup pieces per set, default 4)
  "delay_hours"    : integer 0-24 (hours from now to run; 0 = immediate)
  "destination"    : filesystem path for backup pieces (empty string if not given)
  "delete_input"   : true | false  (delete archivelogs after backing up)
  "delete_obsolete": true | false  (purge obsolete backups afterwards)
  "section_size"   : e.g. "4G" for large files, empty string if not given

Defaults when not mentioned:
  backup_type=full, level=0, tag="", compress=true, encrypt=false, channels=2,
  filesperset=4, delay_hours=0, destination="", delete_input=true,
  delete_obsolete=true, section_size="".

Return ONLY the JSON object — no explanation, no markdown.""",
    ),
    ("human", "{user_query}"),
])

_intent_chain = _INTENT_PROMPT | _llm


# ---------------------------------------------------------------------------
# Keyword fallback — used when LLM is unavailable or returns bad JSON
# ---------------------------------------------------------------------------

def _keyword_fallback(query: str) -> RMANParams:
    """Best-effort keyword extraction so the pipeline never hard-errors."""
    q = query.lower()

    if any(kw in q for kw in ("archivelog", "archive log", "archivelogs")):
        btype = "archivelog"
    elif any(kw in q for kw in ("incremental", "level 1", "level1", "differential")):
        btype = "incremental"
    else:
        btype = "full"

    level = 1 if btype == "incremental" and re.search(r"level\s*1", q) else 0

    compress  = not any(kw in q for kw in ("uncompressed", "no compress", "without compress"))
    encrypt   = any(kw in q for kw in ("encrypt", "encrypted", "encryption"))

    ch_match  = re.search(r"(\d+)\s*(?:channel|parallel)", q)
    channels  = max(1, min(8, int(ch_match.group(1)))) if ch_match else 2

    # Rough delay heuristic: "tonight" ≈ hours until 02:00
    delay_hours = 0
    if any(kw in q for kw in ("tonight", "this evening", "after hours")):
        now  = datetime.now()
        run_at = now.replace(hour=2, minute=0, second=0, microsecond=0)
        if run_at <= now:
            run_at += timedelta(days=1)
        delay_hours = max(0, round((run_at - now).seconds / 3600))

    return RMANParams(
        backup_type     = btype,
        level           = level,
        compress        = compress,
        encrypt         = encrypt,
        channels        = channels,
        delay_hours     = delay_hours,
        delete_input    = True,
        delete_obsolete = True,
    )


# ---------------------------------------------------------------------------
# Public: parse_backup_intent
# ---------------------------------------------------------------------------

def parse_backup_intent(user_query: str) -> RMANParams:
    """
    Convert a natural-language backup request to a structured RMANParams object.

    Primary path  : Ollama (JSON mode) — handles novel phrasing.
    Fallback path : keyword heuristics — activates on any LLM / parse failure
                    so backup requests never produce an unhandled exception.
    """
    try:
        response = _intent_chain.invoke({"user_query": user_query})
        raw      = response.content
        data     = json.loads(raw) if isinstance(raw, str) else raw

        btype = str(data.get("backup_type", "full")).lower()
        if btype not in ("full", "incremental", "archivelog"):
            btype = "full"

        return RMANParams(
            backup_type     = btype,
            level           = max(0, min(1, int(data.get("level", 0)))),
            tag             = str(data.get("tag", "")),
            compress        = bool(data.get("compress", True)),
            encrypt         = bool(data.get("encrypt", False)),
            channels        = max(1, min(8, int(data.get("channels", 2)))),
            filesperset     = max(1, min(16, int(data.get("filesperset", 4)))),
            delay_hours     = max(0, min(24, int(data.get("delay_hours", 0)))),
            destination     = str(data.get("destination", "")),
            delete_input    = bool(data.get("delete_input", True)),
            delete_obsolete = bool(data.get("delete_obsolete", True)),
            section_size    = str(data.get("section_size", "")),
        )

    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("LLM intent parse failed (%s); using keyword fallback.", exc)
        return _keyword_fallback(user_query)


# ---------------------------------------------------------------------------
# Public: generate_rman_script
# ---------------------------------------------------------------------------

def generate_rman_script(params: RMANParams) -> str:
    """
    Deterministically generate a human-reviewable RMAN script from RMANParams.
    No LLM involved. The script is always shown to the DBA before execution.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    tag   = params.tag.upper() if params.tag else f"{params.backup_type.upper()}_{stamp}"

    # Intended execution time shown as a header comment
    if params.delay_hours > 0:
        run_at = datetime.now() + timedelta(hours=params.delay_hours)
        header = (
            f"-- Intended execution time: {run_at.strftime('%Y-%m-%d %H:%M')} "
            f"(+{params.delay_hours}h from approval request)\n"
        )
    else:
        header = f"-- Intended execution time: immediate\n"

    lines: list[str] = [
        header,
        "RUN {",
    ]

    # Channel allocation (with optional FORMAT destination)
    fmt_clause = f" FORMAT '{params.destination}/%U'" if params.destination else ""
    for i in range(1, params.channels + 1):
        lines.append(f"    ALLOCATE CHANNEL c{i} DEVICE TYPE DISK{fmt_clause};")

    # Encryption directive (Oracle Secure Backup / TDE)
    if params.encrypt:
        lines.append("    SET ENCRYPTION ON IDENTIFIED BY GLOBAL KEYSTORE;")

    # Core BACKUP command
    compress_kw  = " AS COMPRESSED BACKUPSET" if params.compress   else ""
    section_kw   = f" SECTION SIZE {params.section_size}"          if params.section_size else ""
    fps_kw       = f" FILESPERSET {params.filesperset}"

    if params.backup_type == "archivelog":
        del_kw = " DELETE ALL INPUT" if params.delete_input else ""
        lines.append(
            f"    BACKUP{compress_kw} ARCHIVELOG ALL{del_kw}{fps_kw} TAG '{tag}';"
        )
    elif params.backup_type == "incremental":
        lines.append(
            f"    BACKUP{compress_kw} INCREMENTAL LEVEL {params.level}{section_kw}"
            f" DATABASE{fps_kw} TAG '{tag}' PLUS ARCHIVELOG DELETE INPUT;"
        )
    else:  # full
        lines.append(
            f"    BACKUP{compress_kw} DATABASE{section_kw}{fps_kw}"
            f" TAG '{tag}' PLUS ARCHIVELOG DELETE INPUT;"
        )

    # Always back up the current controlfile last (protects recovery catalog)
    lines.append(f"    BACKUP CURRENT CONTROLFILE TAG '{tag}_CF';")

    # Release channels
    for i in range(1, params.channels + 1):
        lines.append(f"    RELEASE CHANNEL c{i};")

    lines.append("}")

    # Retention cleanup
    if params.delete_obsolete:
        lines.append("")
        lines.append("DELETE NOPROMPT OBSOLETE;")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public: execute_rman_backup
# ---------------------------------------------------------------------------

def execute_rman_backup(script: str) -> str:
    """
    Execute an approved RMAN script via subprocess.

    SECURITY: Credentials are passed through stdin — never as command-line
    arguments — so the password does not appear in `ps`, /proc, or audit logs.

    AUTH MODES (set RMAN_OS_AUTH=true for OS authentication):
      OS auth     : rman target /                     (oracle OS user, simplest)
      Password auth: CONNECT TARGET user/pass@dsn      (via stdin, default)

    Returns a one-line status string that drives the UI colour coding:
      "RMAN backup SUCCEEDED — ..."   → green  (st.success)
      "RMAN FAILED — ..."             → red    (st.error)
    Should ONLY be called after explicit DBA approval in the HITL workflow.
    """
    if _RMAN_DOCKER:
        # Oracle in a container — exec rman inside it, OS-authenticated as sysdba.
        cmd        = ["docker", "exec", "-i", _RMAN_DOCKER, "rman", "target", "/"]
        rman_input = f"{script}\nEXIT;\n"
    elif _RMAN_OS_AUTH:
        cmd        = ["rman", "target", "/"]
        rman_input = f"{script}\nEXIT;\n"
    else:
        cmd        = ["rman"]
        rman_input = (
            f"CONNECT TARGET {_ORACLE_USER}/{_ORACLE_PASSWORD}@{_RMAN_DSN}\n"
            f"{script}\n"
            "EXIT;\n"
        )

    logger.info("Executing RMAN backup (docker=%s, OS auth=%s).", _RMAN_DOCKER or "no", _RMAN_OS_AUTH)

    try:
        proc = subprocess.run(
            cmd,
            input=rman_input,
            capture_output=True,
            text=True,
            timeout=3600,       # 1-hour hard ceiling — RMAN exits, process is killed
        )
    except FileNotFoundError:
        msg = (
            "RMAN FAILED — 'rman' executable not found. "
            "Ensure $ORACLE_HOME/bin is on PATH for this process."
        )
        logger.error(msg)
        return msg
    except subprocess.TimeoutExpired:
        msg = "RMAN FAILED — backup exceeded 1-hour timeout and was terminated."
        logger.error(msg)
        return msg

    output = proc.stdout + proc.stderr

    # RMAN signals fatal failure with RMAN-00571 banner and a non-zero exit code.
    if proc.returncode != 0 or "RMAN-00571" in output:
        error_lines = [
            ln.strip()
            for ln in output.splitlines()
            if re.search(r"RMAN-\d{5}|ORA-\d{5}", ln)
        ]
        errors = " | ".join(error_lines[:5]) if error_lines else output[-500:].strip()
        msg    = f"RMAN FAILED — {errors}"
        logger.error(msg)
        return msg

    logger.info("RMAN backup completed successfully.")
    return "RMAN backup SUCCEEDED — Recovery Manager complete."
