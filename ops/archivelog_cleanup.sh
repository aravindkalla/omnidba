#!/usr/bin/env bash
#
# archivelog_cleanup.sh — bound Oracle archive-log growth on the demo DB.
#
# Why: the oracle-26ai container runs in ARCHIVELOG mode. Archive logs land in
# /opt/oracle/oradata/dbconfig/FREE/dbs and, left unmanaged, filled the VM disk
# to 97% and jammed the archiver (ORA-00257) — see docs / build-progress memory.
#
# This deletes archive logs older than RETENTION_DAYS via RMAN (which also keeps
# the controlfile in sync) and crosschecks first so RMAN's view matches disk.
# ARCHIVELOG mode stays ON; we just bound retention.
#
# Installed as a host cron (survives container restarts). Runs `docker exec` into
# the container, so it needs docker access (this user is in the docker group).

set -uo pipefail

CONTAINER="${ORACLE_CONTAINER:-oracle-26ai}"
RETENTION_DAYS="${RETENTION_DAYS:-1}"
LOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/logs"
LOG="${LOG_DIR}/archivelog_cleanup.log"

mkdir -p "$LOG_DIR"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "$(ts) $*" >>"$LOG"; }

log "=== cleanup start (container=$CONTAINER retention=${RETENTION_DAYS}d) ==="

# Guard: container must be running.
if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  log "ERROR: container $CONTAINER not running — skipping"
  exit 1
fi

before=$(docker exec "$CONTAINER" bash -lc "df -P -h / | tail -1" 2>/dev/null)
log "disk before: ${before:-unknown}"

# RMAN: crosscheck (reconcile controlfile vs disk), delete expired (gone from
# disk), then delete archive logs older than the retention window.
rman_out=$(docker exec "$CONTAINER" bash -lc "rman target / <<'EOF'
CROSSCHECK ARCHIVELOG ALL;
DELETE NOPROMPT EXPIRED ARCHIVELOG ALL;
DELETE NOPROMPT ARCHIVELOG ALL COMPLETED BEFORE 'SYSDATE-${RETENTION_DAYS}';
EOF" 2>&1)
rc=$?

# Summarize rather than dumping the full RMAN transcript into the log.
deleted=$(echo "$rman_out" | grep -c 'deleted archived log')
log "rman rc=$rc, archive logs deleted=$deleted"
if [ $rc -ne 0 ]; then
  log "RMAN transcript (tail):"
  echo "$rman_out" | tail -15 >>"$LOG"
fi

# Ensure the archive destination is enabled/VALID (recover from a prior wedge).
docker exec "$CONTAINER" bash -lc "sqlplus -s '/ as sysdba' <<'EOF' >/dev/null 2>&1
alter system set log_archive_dest_state_1=enable;
exit;
EOF"

after=$(docker exec "$CONTAINER" bash -lc "df -P -h / | tail -1" 2>/dev/null)
log "disk after:  ${after:-unknown}"
log "=== cleanup done ==="
exit 0
