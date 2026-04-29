#!/usr/bin/env bash
# =============================================================================
# OmniDBA — Mobile Stack Startup
#
# Manages FastAPI backend + Metro bundler independently of Streamlit.
# Streamlit is left completely untouched.
#
# USAGE
#   ./startup-mobile.sh start    Start FastAPI + Metro bundler
#   ./startup-mobile.sh stop     Stop FastAPI + Metro bundler
#   ./startup-mobile.sh status   Health-check mobile stack
#   ./startup-mobile.sh restart  Stop then start
#
# EXPO GO (Android)
#   Enter URL manually: exp://34.14.171.170:8081
# =============================================================================
set -euo pipefail

APP_DIR="/opt/oracle-dba-agent"
MOBILE_DIR="$APP_DIR/mobile"
VENV_DIR="$APP_DIR/.venv"
ENV_FILE="$APP_DIR/.env"
LOG_DIR="$APP_DIR/logs"
API_PID_FILE="$APP_DIR/api.pid"
METRO_PID_FILE="$APP_DIR/metro.pid"
PUBLIC_IP="34.14.171.170"
API_PORT="${API_PORT:-8000}"
METRO_PORT=8081

[[ -f "$ENV_FILE" ]] && { set -o allexport; source "$ENV_FILE"; set +o allexport; }

R='\033[0m'; BOLD='\033[1m'
GRN='\033[0;32m'; YLW='\033[1;33m'; RED='\033[0;31m'; CYN='\033[0;36m'; BLU='\033[0;34m'
info()    { echo -e "${CYN}[INFO]${R}   $*"; }
ok()      { echo -e "${GRN}[OK]${R}     $*"; }
warn()    { echo -e "${YLW}[WARN]${R}   $*"; }
fail()    { echo -e "${RED}[ERROR]${R}  $*" >&2; exit 1; }
section() { echo -e "\n${BOLD}${BLU}━━  $*  ━━${R}"; }

# =============================================================================
#  START
# =============================================================================
cmd_start() {
  section "OmniDBA — Mobile Stack"
  mkdir -p "$LOG_DIR"

  # ── FastAPI ────────────────────────────────────────────────────────────────
  if [[ -f "$API_PID_FILE" ]] && kill -0 "$(cat "$API_PID_FILE")" 2>/dev/null; then
    ok "FastAPI already running  (PID=$(cat "$API_PID_FILE"))  →  http://localhost:${API_PORT}/docs"
  else
    local API_LOG="$LOG_DIR/api_$(date +%Y%m%d_%H%M%S).log"
    info "Starting FastAPI on port ${API_PORT}..."
    cd "$APP_DIR"
    nohup "$VENV_DIR/bin/uvicorn" api:app \
      --host 0.0.0.0 \
      --port "$API_PORT" \
      --log-level info \
      >> "$API_LOG" 2>&1 &
    local API_PID=$!
    echo "$API_PID" > "$API_PID_FILE"
    sleep 2
    if kill -0 "$API_PID" 2>/dev/null; then
      ok "FastAPI started  (PID=$API_PID)  →  http://localhost:${API_PORT}/docs"
    else
      warn "FastAPI may have failed — check $API_LOG"
      rm -f "$API_PID_FILE"
    fi
  fi

  # ── Metro bundler ─────────────────────────────────────────────────────────
  if [[ -f "$METRO_PID_FILE" ]] && kill -0 "$(cat "$METRO_PID_FILE")" 2>/dev/null; then
    ok "Metro already running   (PID=$(cat "$METRO_PID_FILE"))  →  exp://${PUBLIC_IP}:${METRO_PORT}"
  else
    [[ -d "$MOBILE_DIR/node_modules" ]] || fail "node_modules missing. Run: cd $MOBILE_DIR && npm install --legacy-peer-deps"
    local METRO_LOG="$LOG_DIR/metro_$(date +%Y%m%d_%H%M%S).log"
    info "Starting Metro bundler  →  exp://${PUBLIC_IP}:${METRO_PORT}"
    cd "$MOBILE_DIR"
    REACT_NATIVE_PACKAGER_HOSTNAME="$PUBLIC_IP" \
    nohup npx expo start --clear >> "$METRO_LOG" 2>&1 &
    local METRO_PID=$!
    echo "$METRO_PID" > "$METRO_PID_FILE"
    sleep 5
    if kill -0 "$METRO_PID" 2>/dev/null; then
      ok "Metro started  (PID=$METRO_PID)"
    else
      warn "Metro may have failed — check $METRO_LOG"
      rm -f "$METRO_PID_FILE"
    fi
  fi

  echo ""
  echo -e "${BOLD}  Expo Go URL : exp://${PUBLIC_IP}:${METRO_PORT}${R}"
  echo -e "  API docs    : http://${PUBLIC_IP}:${API_PORT}/docs"
  echo -e "  Logs        : $LOG_DIR"
  echo ""
}

# =============================================================================
#  STOP
# =============================================================================
cmd_stop() {
  section "Stopping Mobile Stack"

  # Metro
  if [[ -f "$METRO_PID_FILE" ]]; then
    local pid; pid=$(cat "$METRO_PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      pkill -P "$pid" 2>/dev/null || true
      kill "$pid" 2>/dev/null && ok "Metro stopped (PID=$pid)"
    else
      warn "Metro PID $pid was not running"
    fi
    rm -f "$METRO_PID_FILE"
  else
    pkill -f "expo start" 2>/dev/null && ok "Metro stopped" || warn "Metro was not running"
  fi

  # FastAPI
  if [[ -f "$API_PID_FILE" ]]; then
    local pid; pid=$(cat "$API_PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null && ok "FastAPI stopped (PID=$pid)"
    else
      warn "FastAPI PID $pid was not running"
    fi
    rm -f "$API_PID_FILE"
  else
    pkill -f "uvicorn api:app" 2>/dev/null && ok "FastAPI stopped" || warn "FastAPI was not running"
  fi
}

# =============================================================================
#  STATUS
# =============================================================================
cmd_status() {
  section "OmniDBA - Mobile Stack Health"

  # FastAPI
  if curl -sf "http://localhost:${API_PORT}/status" &>/dev/null; then
    local s; s=$(curl -sf "http://localhost:${API_PORT}/status")
    ok "FastAPI     running  →  http://localhost:${API_PORT}  $s"
  else
    warn "FastAPI     NOT running  →  $0 start"
  fi

  # Metro
  if curl -sf "http://localhost:${METRO_PORT}/status" &>/dev/null; then
    ok "Metro       running  →  exp://${PUBLIC_IP}:${METRO_PORT}"
  else
    warn "Metro       NOT running  →  $0 start"
  fi

  # Nginx
  if curl -sk "https://localhost/api/status" &>/dev/null; then
    ok "Nginx HTTPS running  →  https://${PUBLIC_IP}/api/status"
  else
    warn "Nginx HTTPS NOT running  →  sudo systemctl start nginx"
  fi
}

# =============================================================================
#  ENTRY POINT
# =============================================================================
case "${1:-help}" in
  start)   cmd_start  ;;
  stop)    cmd_stop   ;;
  status)  cmd_status ;;
  restart) cmd_stop; sleep 2; cmd_start ;;
  help|-h|--help)
    echo -e "${BOLD}OmniDBA — Mobile Stack${R}"
    echo ""
    echo -e "  ${BOLD}$0 start${R}    Start FastAPI + Metro bundler"
    echo -e "  ${BOLD}$0 stop${R}     Stop FastAPI + Metro bundler"
    echo -e "  ${BOLD}$0 status${R}   Health-check mobile stack"
    echo -e "  ${BOLD}$0 restart${R}  Restart both services"
    echo ""
    echo -e "  Expo Go URL : ${BOLD}exp://${PUBLIC_IP}:${METRO_PORT}${R}"
    ;;
  *) warn "Unknown command: $1"; echo ""; exec "$0" help; exit 1 ;;
esac
