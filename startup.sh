#!/usr/bin/env bash
# =============================================================================
# OmniDBA — Setup & Startup Script
# =============================================================================
#
# USAGE
#   sudo ./startup.sh setup    First-time full installation (requires sudo)
#        ./startup.sh start    Start Streamlit app (services must already be up)
#        ./startup.sh train    Re-run Vanna RAG schema ingestion
#        ./startup.sh status   Health-check all services
#        ./startup.sh stop     Stop Streamlit (and optionally Ollama)
#
# ENVIRONMENT  (override in .env or via shell export before running)
#   ORACLE_USER       Oracle username              default: admin
#   ORACLE_PASSWORD   Oracle password              default: Welcome1
#   ORACLE_DSN        App DB DSN (PDB)             default: localhost:1521/FREEPDB1
#   RMAN_DSN          RMAN CDB DSN                 default: localhost:1521/FREE
#   OLLAMA_MODEL      Model to pull & use          default: llama3.1
#   OLLAMA_HOST       Ollama API base URL          default: http://localhost:11434
#   CHROMA_PATH       ChromaDB directory           default: /opt/oracle-dba-agent/chroma_db
#   APP_PORT          Streamlit port               default: 8501
#   RMAN_OS_AUTH      Use OS auth for RMAN         default: false
# =============================================================================
set -euo pipefail

# ── Fixed paths ────────────────────────────────────────────────────────────
APP_DIR="/opt/oracle-dba-agent"
VENV_DIR="$APP_DIR/.venv"
ENV_FILE="$APP_DIR/.env"
LOG_DIR="$APP_DIR/logs"
REAL_USER="${SUDO_USER:-$USER}"
ONNX_DIR="/home/$REAL_USER/.cache/chroma/onnx_models/all-MiniLM-L6-v2"

# ── Default env values (overridden by .env if present) ────────────────────
ORACLE_USER="${ORACLE_USER:-admin}"
ORACLE_PASSWORD="${ORACLE_PASSWORD:-Welcome1}"
ORACLE_DSN="${ORACLE_DSN:-localhost:1521/FREEPDB1}"
RMAN_DSN="${RMAN_DSN:-localhost:1521/FREE}"
OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.1}"
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
CHROMA_PATH="${CHROMA_PATH:-$APP_DIR/chroma_db}"
APP_PORT="${APP_PORT:-8501}"
RMAN_OS_AUTH="${RMAN_OS_AUTH:-false}"
APP_PORT="${APP_PORT:-8501}"
API_PORT="${API_PORT:-8000}"
API_PID_FILE="$APP_DIR/api.pid"
SL_PID_FILE="$APP_DIR/streamlit.pid"

# Load .env if it exists
[[ -f "$ENV_FILE" ]] && { set -o allexport; source "$ENV_FILE"; set +o allexport; }

# ── Terminal colours ────────────────────────────────────────────────────────
R='\033[0m'; BOLD='\033[1m'
GRN='\033[0;32m'; YLW='\033[1;33m'; RED='\033[0;31m'
CYN='\033[0;36m'; BLU='\033[0;34m'

info()    { echo -e "${CYN}[INFO]${R}   $*"; }
ok()      { echo -e "${GRN}[OK]${R}     $*"; }
warn()    { echo -e "${YLW}[WARN]${R}   $*"; }
fail()    { echo -e "${RED}[ERROR]${R}  $*" >&2; exit 1; }
section() { echo -e "\n${BOLD}${BLU}━━  $*  ━━${R}"; }

# ── Usage ──────────────────────────────────────────────────────────────────
_usage() {
  echo -e "${BOLD}OmniDBA — startup.sh${R}"
  echo ""
  echo -e "  ${BOLD}sudo $0 setup${R}      Full first-time installation"
  echo -e "       ${BOLD}$0 start${R}      Foreground — live logs in terminal, Ctrl+C stops everything"
  echo -e "       ${BOLD}$0 start-bg${R}   Background — nohup, survives SSH disconnect, use 'stop' to halt"
  echo -e "       ${BOLD}$0 train${R}      Re-run Vanna RAG ingestion"
  echo -e "       ${BOLD}$0 status${R}     Health-check all services"
  echo -e "       ${BOLD}$0 stop${R}       Stop all app processes"
  echo ""
  echo "  Config file : $ENV_FILE"
  echo "  Web UI      : http://localhost:${APP_PORT}"
  echo "  Mobile API  : http://localhost:${API_PORT}   (docs: /docs)"
}

# =============================================================================
#  SETUP  — complete first-time installation
# =============================================================================
cmd_setup() {
  [[ $EUID -ne 0 ]] && fail "'setup' must be run with sudo"

  # ── 1. System packages ────────────────────────────────────────────────────
  section "Step 1 / 7 — System dependencies"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    docker.io python3-pip python3-venv curl tar git ca-certificates
  ok "System packages installed"

  # ── 2. Oracle 26ai Free (Docker) ─────────────────────────────────────────
  section "Step 2 / 7 — Oracle 26ai Free (Docker)"
  systemctl start  docker
  systemctl enable docker

  if docker ps -a --format '{{.Names}}' | grep -q '^oracle-26ai$'; then
    info "Container 'oracle-26ai' already exists — starting if stopped"
    docker start oracle-26ai 2>/dev/null || true
  else
    info "Pulling oracle/free:latest and starting container..."
    info "Note: first initialisation takes 5-10 minutes."
    docker run -d \
      --name oracle-26ai \
      --restart unless-stopped \
      -p 1521:1521 \
      -e ORACLE_PASSWORD="$ORACLE_PASSWORD" \
      -v oracle-data:/opt/oracle/oradata \
      container-registry.oracle.com/database/free:latest
  fi
  _wait_for_oracle

  # ── 3. Ollama LLM engine ──────────────────────────────────────────────────
  section "Step 3 / 7 — Ollama"
  if ! command -v ollama &>/dev/null; then
    info "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
  else
    info "Ollama already installed: $(ollama --version 2>/dev/null || echo '?')"
  fi
  systemctl start  ollama 2>/dev/null || true
  systemctl enable ollama 2>/dev/null || true
  _wait_for_ollama
  info "Pulling model: $OLLAMA_MODEL (may take several minutes on first run)"
  ollama pull "$OLLAMA_MODEL"
  ok "Model '$OLLAMA_MODEL' ready"

  # ── 4. ChromaDB ONNX embedding model ─────────────────────────────────────
  section "Step 4 / 7 — ChromaDB ONNX embedding model"
  _install_onnx_model

  # ── 5. Python virtual environment ────────────────────────────────────────
  section "Step 5 / 7 — Python virtual environment"
  _setup_venv

  # ── 6. Environment file ───────────────────────────────────────────────────
  section "Step 6 / 7 — Environment file"
  _write_env_file

  # ── 7. Vanna RAG training ─────────────────────────────────────────────────
  section "Step 7 / 7 — Vanna RAG schema training"
  _run_training

  echo ""
  echo -e "${BOLD}${GRN}✔  Setup complete.${R}"
  echo ""
  echo -e "  Start the app :  ${BOLD}$0 start${R}"
  echo -e "  App URL       :  ${BOLD}http://localhost:${APP_PORT}${R}"
  echo -e "  Config file   :  ${BOLD}$ENV_FILE${R}"
  echo ""
}

# =============================================================================
#  SHARED — pre-flight checks + FastAPI launch (used by both start modes)
# =============================================================================
_preflight() {
  [[ -f "$ENV_FILE" ]] && { set -o allexport; source "$ENV_FILE"; set +o allexport; } \
    || warn ".env not found — using built-in defaults"
  _check_oracle_running
  _check_ollama_running
  [[ -x "$VENV_DIR/bin/streamlit" ]] || fail "venv not set up. Run: sudo $0 setup"
  mkdir -p "$LOG_DIR"
}

_start_fastapi() {
  _stop_api 2>/dev/null || true
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
}

# =============================================================================
#  START  — foreground mode (live logs, Ctrl+C stops everything)
# =============================================================================
cmd_start() {
  section "Starting OmniDBA  [foreground]"
  _preflight
  _start_fastapi

  local SL_LOG="$LOG_DIR/streamlit_$(date +%Y%m%d_%H%M%S).log"
  info "Starting Streamlit on port ${APP_PORT}  (Ctrl+C to stop both services)"
  info "Web UI : http://localhost:${APP_PORT}"
  info "API    : http://localhost:${API_PORT}"
  echo ""

  # Stop FastAPI when Ctrl+C is pressed
  trap '_stop_api; exit 0' INT TERM

  cd "$APP_DIR"
  "$VENV_DIR/bin/streamlit" run app.py \
    --server.port                      "$APP_PORT" \
    --server.address                   "0.0.0.0" \
    --server.headless                  true \
    --server.enableCORS                false \
    --server.enableXsrfProtection      false \
    --server.enableWebsocketCompression false \
    --browser.gatherUsageStats         false \
    2>&1 | tee "$SL_LOG"
}

# =============================================================================
#  START-BG  — background mode (nohup, survives SSH disconnect)
# =============================================================================
cmd_start_bg() {
  section "Starting OmniDBA  [background]"
  _preflight
  _start_fastapi

  local SL_LOG="$LOG_DIR/streamlit_$(date +%Y%m%d_%H%M%S).log"

  if [[ -f "$SL_PID_FILE" ]] && kill -0 "$(cat "$SL_PID_FILE")" 2>/dev/null; then
    ok "Streamlit already running  (PID=$(cat "$SL_PID_FILE"))  →  http://localhost:${APP_PORT}"
  else
    info "Starting Streamlit on port ${APP_PORT}..."
    cd "$APP_DIR"
    nohup "$VENV_DIR/bin/streamlit" run app.py \
      --server.port                      "$APP_PORT" \
      --server.address                   "0.0.0.0" \
      --server.headless                  true \
      --server.enableCORS                false \
      --server.enableXsrfProtection      false \
      --server.enableWebsocketCompression false \
      --browser.gatherUsageStats         false \
      >> "$SL_LOG" 2>&1 &
    local SL_PID=$!
    echo "$SL_PID" > "$SL_PID_FILE"
    sleep 3
    if kill -0 "$SL_PID" 2>/dev/null; then
      ok "Streamlit started  (PID=$SL_PID)  →  http://localhost:${APP_PORT}"
    else
      warn "Streamlit may have failed — check $SL_LOG"
      rm -f "$SL_PID_FILE"
    fi
  fi

  echo ""
  echo -e "${BOLD}  Web UI  :  http://localhost:${APP_PORT}${R}"
  echo -e "  API     :  http://localhost:${API_PORT}"
  echo -e "  Logs    :  $LOG_DIR"
  echo -e "  Stop    :  $0 stop"
  echo ""
}

# =============================================================================
#  TRAIN  — re-run Vanna RAG ingestion
# =============================================================================
cmd_train() {
  section "Vanna RAG schema ingestion"
  [[ -f "$ENV_FILE" ]] && { set -o allexport; source "$ENV_FILE"; set +o allexport; }
  _check_oracle_running
  _run_training
}

# =============================================================================
#  STATUS  — health dashboard
# =============================================================================
cmd_status() {
  section "OmniDBA - Service health"

  # Oracle container
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^oracle-26ai$'; then
    local health
    health=$(docker inspect oracle-26ai --format '{{.State.Health.Status}}' 2>/dev/null || echo "no healthcheck")
    ok   "Oracle 26ai    running  [$health]"
  else
    warn "Oracle 26ai    NOT running  →  sudo docker start oracle-26ai"
  fi

  # Ollama
  if curl -sf "$OLLAMA_HOST/api/tags" &>/dev/null; then
    local models
    models=$(curl -sf "$OLLAMA_HOST/api/tags" \
      | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(', '.join(m['name'] for m in d.get('models', [])) or 'no models pulled')
" 2>/dev/null || echo "?")
    ok   "Ollama         running  [models: $models]"
  else
    warn "Ollama         NOT running  →  sudo systemctl start ollama"
  fi

  # FastAPI backend
  if curl -sf "http://localhost:${API_PORT}/status" &>/dev/null; then
    ok   "FastAPI API    running  [http://localhost:${API_PORT}]  [/docs]"
  else
    warn "FastAPI API    NOT running  →  $0 start"
  fi

  # Streamlit
  if curl -sf "http://localhost:${APP_PORT}/_stcore/health" &>/dev/null; then
    ok   "Streamlit      running  [http://localhost:${APP_PORT}]"
  else
    warn "Streamlit      NOT running  →  $0 start"
  fi

  # ONNX model (ChromaDB extracts to onnx/model.onnx subdirectory)
  if [[ -f "$ONNX_DIR/onnx/model.onnx" ]] || [[ -f "$ONNX_DIR/model.onnx" ]]; then
    ok   "ONNX model     present  [$ONNX_DIR]"
  else
    warn "ONNX model     missing  →  sudo $0 setup"
  fi

  # Python venv
  if [[ -x "$VENV_DIR/bin/python" ]]; then
    ok   "Python venv    $("$VENV_DIR/bin/python" --version 2>&1)  [$VENV_DIR]"
  else
    warn "Python venv    missing  →  sudo $0 setup"
  fi

  # .env file
  if [[ -f "$ENV_FILE" ]]; then
    ok   ".env file      $ENV_FILE"
  else
    warn ".env file      not found — using defaults"
  fi

  # ChromaDB
  if [[ -f "$CHROMA_PATH/chroma.sqlite3" ]]; then
    local sz
    sz=$(du -sh "$CHROMA_PATH" 2>/dev/null | cut -f1)
    ok   "ChromaDB       $CHROMA_PATH  [$sz]"
  else
    warn "ChromaDB       empty or missing — run: $0 train"
  fi
}

# =============================================================================
#  STOP
# =============================================================================
cmd_stop() {
  section "Stopping services"

  # Streamlit
  if [[ -f "$SL_PID_FILE" ]]; then
    local pid; pid=$(cat "$SL_PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null && ok "Streamlit stopped (PID=$pid)"
    else
      warn "Streamlit PID $pid was not running"
    fi
    rm -f "$SL_PID_FILE"
  else
    pkill -f "streamlit run" 2>/dev/null && ok "Streamlit stopped" || warn "Streamlit was not running"
  fi

  # FastAPI
  _stop_api

  read -r -p "Also stop Ollama? [y/N] " ans
  if [[ "${ans,,}" == "y" ]]; then
    sudo systemctl stop ollama && ok "Ollama stopped" || warn "Could not stop Ollama (try: sudo systemctl stop ollama)"
  fi
}

# =============================================================================
#  HELPERS
# =============================================================================

_wait_for_oracle() {
  info "Waiting for Oracle to be healthy (timeout: 10 min)..."
  local n=0
  while true; do
    local st
    st=$(docker inspect oracle-26ai --format '{{.State.Health.Status}}' 2>/dev/null || echo "starting")
    [[ "$st" == "healthy" ]] && { echo ""; ok "Oracle 26ai is healthy"; return; }
    n=$((n+1))
    if [[ $n -ge 60 ]]; then
      echo ""
      warn "Timed out waiting for Oracle. It may still be initialising."
      warn "Check progress: docker logs -f oracle-26ai"
      return
    fi
    printf "."; sleep 10
  done
}

_wait_for_ollama() {
  info "Waiting for Ollama API at $OLLAMA_HOST ..."
  local n=0
  until curl -sf "$OLLAMA_HOST/api/tags" &>/dev/null; do
    n=$((n+1))
    [[ $n -ge 30 ]] && fail "Ollama did not respond after 60 s. Check: systemctl status ollama"
    sleep 2
  done
  ok "Ollama API ready"
}

_install_onnx_model() {
  if [[ -f "$ONNX_DIR/model.onnx" ]]; then
    ok "ONNX model already present — skipping download"
    return
  fi
  info "Downloading all-MiniLM-L6-v2 ONNX model (ChromaDB embeddings)..."
  mkdir -p "$ONNX_DIR"
  curl -k -L \
    "https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz" \
    -o "$ONNX_DIR/onnx.tar.gz"
  tar -xzf "$ONNX_DIR/onnx.tar.gz" -C "$ONNX_DIR/"
  rm  -f   "$ONNX_DIR/onnx.tar.gz"
  # Restore ownership to the non-root user that will run the app
  chown -R "$REAL_USER:$REAL_USER" "/home/$REAL_USER/.cache"
  ok "ONNX model installed → $ONNX_DIR"
}

_setup_venv() {
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    info "Creating Python 3 virtual environment at $VENV_DIR ..."
    python3 -m venv "$VENV_DIR"
    chown -R "$REAL_USER:$REAL_USER" "$VENV_DIR"
  fi
  info "Installing/upgrading Python dependencies..."
  "$VENV_DIR/bin/pip" install --quiet --upgrade pip setuptools wheel
  "$VENV_DIR/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
  ok "venv ready: $("$VENV_DIR/bin/python" --version)"
}

_write_env_file() {
  if [[ -f "$ENV_FILE" ]]; then
    info ".env already exists — leaving it unchanged"
    info "Edit $ENV_FILE to change connection settings."
    # Add JWT_SECRET_KEY if it was added after initial setup
    if ! grep -q "JWT_SECRET_KEY" "$ENV_FILE"; then
      local secret
      secret=$(python3 -c "import secrets; print(secrets.token_hex(32))")
      echo "" >> "$ENV_FILE"
      echo "# ── Mobile API ────────────────────────────────────────────────" >> "$ENV_FILE"
      echo "JWT_SECRET_KEY=$secret" >> "$ENV_FILE"
      echo "API_PORT=8000" >> "$ENV_FILE"
      ok "JWT_SECRET_KEY added to existing .env"
    fi
    return
  fi
  local JWT_SECRET
  JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  cat > "$ENV_FILE" <<EOF
# OmniDBA — Environment Configuration
# Generated by startup.sh on $(date '+%Y-%m-%d %H:%M:%S')
# This file is chmod 600 — keep it private.

# ── Oracle DB ──────────────────────────────────────────────────
ORACLE_USER=$ORACLE_USER
ORACLE_PASSWORD=$ORACLE_PASSWORD
ORACLE_DSN=$ORACLE_DSN

# RMAN connects to the CDB root (not the PDB)
RMAN_DSN=$RMAN_DSN

# Set to true to use OS authentication (rman target /)
# Requires this process to run as the oracle OS user
RMAN_OS_AUTH=$RMAN_OS_AUTH

# ── Ollama ─────────────────────────────────────────────────────
OLLAMA_MODEL=$OLLAMA_MODEL
OLLAMA_HOST=$OLLAMA_HOST

# ── Application ────────────────────────────────────────────────
CHROMA_PATH=$CHROMA_PATH
APP_PORT=$APP_PORT

# ── Mobile API ─────────────────────────────────────────────────
# Cryptographically random secret — do not share or commit to git
JWT_SECRET_KEY=$JWT_SECRET
API_PORT=8000
EOF
  chmod 600 "$ENV_FILE"
  chown "$REAL_USER:$REAL_USER" "$ENV_FILE"
  ok ".env written → $ENV_FILE  (chmod 600)"
}

_run_training() {
  info "Running Vanna schema ingestion against Oracle ($ORACLE_DSN) ..."
  cd "$APP_DIR"
  "$VENV_DIR/bin/python" - <<'PYEOF'
import os, sys
sys.path.insert(0, "/opt/oracle-dba-agent")

# Surface env vars to the modules
from diagnostic_agent import train_on_oracle_schema
try:
    train_on_oracle_schema()
    print("[OK] Vanna training complete.")
except Exception as exc:
    print(f"[WARN] Training error (non-fatal — app will still start): {exc}", file=sys.stderr)
PYEOF
  ok "Vanna RAG training finished"
}

_stop_api() {
  if [[ -f "$API_PID_FILE" ]]; then
    local pid
    pid=$(cat "$API_PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && ok "FastAPI stopped (PID=$pid)"
    else
      warn "FastAPI PID $pid was not running"
    fi
    rm -f "$API_PID_FILE"
  elif pkill -f "uvicorn api:app" 2>/dev/null; then
    ok "FastAPI stopped"
  else
    warn "FastAPI was not running"
  fi
}

_check_oracle_running() {
  if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^oracle-26ai$'; then
    warn "Oracle container is not running — attempting docker start ..."
    docker start oracle-26ai 2>/dev/null \
      || fail "Cannot start Oracle. Run: sudo docker start oracle-26ai"
    _wait_for_oracle
  fi
}

_check_ollama_running() {
  if ! curl -sf "$OLLAMA_HOST/api/tags" &>/dev/null; then
    warn "Ollama not responding — attempting to start ..."
    systemctl start ollama 2>/dev/null || ollama serve >> "$LOG_DIR/ollama.log" 2>&1 &
    _wait_for_ollama
  fi
}

# =============================================================================
#  ENTRY POINT
# =============================================================================
case "${1:-help}" in
  setup)    cmd_setup    ;;
  start)    cmd_start    ;;
  start-bg) cmd_start_bg ;;
  train)    cmd_train    ;;
  status)   cmd_status   ;;
  stop)     cmd_stop     ;;
  help|-h|--help) _usage ;;
  *) warn "Unknown command: $1"; echo ""; _usage; exit 1 ;;
esac
