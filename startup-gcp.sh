#!/usr/bin/env bash
# =============================================================================
# OmniDBA v3 (GCP/Vertex) — Startup Script   [worktree /opt/omnidba-gcp]
# =============================================================================
#
# USAGE
#   ./startup-gcp.sh start     Start the v3 Streamlit demo UI (:8610)
#   ./startup-gcp.sh stop      Stop the v3 Streamlit UI
#   ./startup-gcp.sh status    Health-check backends (containers, ADC, port)
#   ./startup-gcp.sh tunnel    Print the SSH tunnel command to view the UI
#   ./startup-gcp.sh restart   stop + start
#
# BACKEND SELECTION  (override in .env or via shell export before running)
#   LLM_PROVIDER   vertex | ollama    default: vertex   (ollama = air-gapped)
#   DB_ENGINE      oracle | postgres  default: oracle
# e.g.  LLM_PROVIDER=ollama DB_ENGINE=postgres ./startup-gcp.sh restart
#
# Does NOT touch v1 (frozen on main at /opt/oracle-dba-agent, ports 8000/8501).
# v3 ports: 8610 Streamlit · 8600 API · 1521 Oracle · 5433 Postgres.
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

VENV="$HERE/.venv-gcp"
LOG="$HERE/ops/logs/streamlit.log"
APP_PORT="${APP_PORT:-8610}"
EXTERNAL_IP="34.14.171.170"

# Load .env (gitignored) so backend + creds are set.
if [[ -f "$HERE/.env" ]]; then set -a; . "$HERE/.env"; set +a; fi
LLM_PROVIDER="${LLM_PROVIDER:-vertex}"
DB_ENGINE="${DB_ENGINE:-oracle}"

log()  { echo -e "  $*"; }
ok()   { echo -e "  \033[32m✔\033[0m $*"; }
warn() { echo -e "  \033[33m!\033[0m $*"; }
err()  { echo -e "  \033[31mx\033[0m $*"; }

check_backends() {
  echo "── Backends ──────────────────────────────────────────"
  log "LLM_PROVIDER=$LLM_PROVIDER   DB_ENGINE=$DB_ENGINE"

  # Oracle container
  if docker ps --format '{{.Names}}' | grep -q '^oracle-26ai$'; then ok "Oracle container up (:1521)"
  else warn "Oracle container 'oracle-26ai' not running"; fi

  # Postgres container
  if docker ps --format '{{.Names}}' | grep -q '^omnidba-pg$'; then ok "Postgres container up (:5433)"
  else warn "Postgres container 'omnidba-pg' not running"; fi

  # Ollama (only needed for the air-gapped path)
  if [[ "$LLM_PROVIDER" == "ollama" ]]; then
    if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then ok "Ollama reachable (:11434)"
    else warn "Ollama not reachable — LLM_PROVIDER=ollama needs it"; fi
  fi

  # Vertex ADC (only needed for the cloud path)
  if [[ "$LLM_PROVIDER" == "vertex" ]]; then
    if gcloud auth application-default print-access-token >/dev/null 2>&1; then
      ok "GCP ADC present (Vertex/BigQuery/Cloud Tasks)"
    else
      err "No ADC — run: gcloud auth application-default login"
    fi
  fi
}

cmd_status() {
  check_backends
  echo "── UI ────────────────────────────────────────────────"
  if curl -sf -o /dev/null "http://localhost:${APP_PORT}/_stcore/health" 2>/dev/null; then
    ok "Streamlit UP on :${APP_PORT}"
  else
    warn "Streamlit not responding on :${APP_PORT}"
  fi
}

cmd_start() {
  check_backends
  echo "── Start UI ──────────────────────────────────────────"
  if curl -sf -o /dev/null "http://localhost:${APP_PORT}/_stcore/health" 2>/dev/null; then
    ok "Already running on :${APP_PORT} — use 'restart' to relaunch"
    return 0
  fi
  mkdir -p "$HERE/ops/logs"
  LLM_PROVIDER="$LLM_PROVIDER" DB_ENGINE="$DB_ENGINE" \
    nohup "$VENV/bin/streamlit" run app.py \
      --server.port "$APP_PORT" --server.address 0.0.0.0 \
      --server.headless true --browser.gatherUsageStats false \
      > "$LOG" 2>&1 &
  log "launched pid $! → $LOG"
  # wait for health (retry on connection-refused, no sleep)
  if curl -s --retry 20 --retry-delay 1 --retry-connrefused -o /dev/null \
       "http://localhost:${APP_PORT}/_stcore/health" 2>/dev/null; then
    ok "Streamlit UP on :${APP_PORT}  [${LLM_PROVIDER} · ${DB_ENGINE}]"
    cmd_tunnel
  else
    err "Streamlit failed to come up — check: tail -30 $LOG"
    return 1
  fi
}

cmd_stop() {
  echo "── Stop UI ───────────────────────────────────────────"
  # Match this worktree's streamlit on the demo port only (don't kill v1).
  local pids
  pids="$(pgrep -f "streamlit run app.py.*--server.port ${APP_PORT}" || true)"
  if [[ -n "$pids" ]]; then
    kill $pids && ok "stopped streamlit ($pids)"
  else
    warn "no streamlit on :${APP_PORT} to stop"
  fi
}

cmd_tunnel() {
  echo "── View from your laptop ─────────────────────────────"
  log "ssh -N -L ${APP_PORT}:localhost:${APP_PORT} aravind_kalla@${EXTERNAL_IP}"
  log "then open  http://localhost:${APP_PORT}"
}

case "${1:-start}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status)  cmd_status ;;
  tunnel)  cmd_tunnel ;;
  *) echo "usage: $0 {start|stop|restart|status|tunnel}"; exit 2 ;;
esac
