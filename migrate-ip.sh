#!/usr/bin/env bash
# =============================================================================
# OmniDBA — VM IP Migration Script
#
# Spot/preemptible VMs get a new public IP on every restart.
# This script updates all hardcoded IPs across the project in one shot.
#
# USAGE
#   sudo ./migrate-ip.sh              Auto-detect new IP from GCP metadata
#   sudo ./migrate-ip.sh 35.x.x.x    Manually specify new IP
#
# WHAT IT UPDATES
#   generate_ssl.sh           SERVER_IP variable
#   startup-mobile.sh         PUBLIC_IP variable + comment
#   mobile/app.json           extra.apiUrl
#   mobile/stores/useAppStore.ts  apiUrl default
#   /etc/ssl/oracle-dba/      Regenerates SSL cert with new IP in SAN
#   nginx                     Reloads to pick up new cert
# =============================================================================
set -euo pipefail

APP_DIR="/opt/oracle-dba-agent"

R='\033[0m'; BOLD='\033[1m'
GRN='\033[0;32m'; YLW='\033[1;33m'; RED='\033[0;31m'; CYN='\033[0;36m'; BLU='\033[0;34m'
info()    { echo -e "${CYN}[INFO]${R}   $*"; }
ok()      { echo -e "${GRN}[OK]${R}     $*"; }
warn()    { echo -e "${YLW}[WARN]${R}   $*"; }
fail()    { echo -e "${RED}[ERROR]${R}  $*" >&2; exit 1; }
section() { echo -e "\n${BOLD}${BLU}━━  $*  ━━${R}"; }

# ── Must run as root (for SSL cert + nginx reload) ─────────────────────────
[[ $EUID -ne 0 ]] && fail "Run with sudo: sudo $0 [new-ip]"

# ── Detect old IP from generate_ssl.sh ────────────────────────────────────
OLD_IP=$(grep -oP 'SERVER_IP="\K[^"]+' "$APP_DIR/generate_ssl.sh" 2>/dev/null || echo "")
[[ -z "$OLD_IP" ]] && fail "Could not detect current IP from generate_ssl.sh"

# ── Resolve new IP ─────────────────────────────────────────────────────────
if [[ -n "${1:-}" ]]; then
  NEW_IP="$1"
  info "Using manually specified IP: $NEW_IP"
else
  info "Auto-detecting public IP from GCP metadata..."
  NEW_IP=$(curl -sf --connect-timeout 3 \
    -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/externalIp" \
    2>/dev/null || true)

  # Fallback to external IP check services
  if [[ -z "$NEW_IP" ]]; then
    NEW_IP=$(curl -sf --connect-timeout 3 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)
  fi
  if [[ -z "$NEW_IP" ]]; then
    NEW_IP=$(curl -sf --connect-timeout 3 https://ifconfig.me 2>/dev/null | tr -d '[:space:]' || true)
  fi
  [[ -z "$NEW_IP" ]] && fail "Could not auto-detect public IP. Pass it manually: sudo $0 <new-ip>"
  info "Detected new public IP: $NEW_IP"
fi

# ── Validate IP format ──────────────────────────────────────────────────────
if ! echo "$NEW_IP" | grep -qP '^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$'; then
  fail "Invalid IP format: $NEW_IP"
fi

# ── No-op check ────────────────────────────────────────────────────────────
if [[ "$OLD_IP" == "$NEW_IP" ]]; then
  ok "IP is unchanged ($NEW_IP) — nothing to do."
  exit 0
fi

section "VM IP Migration: $OLD_IP → $NEW_IP"

# ── Helper: replace in file ────────────────────────────────────────────────
replace_in_file() {
  local file="$1"
  if [[ ! -f "$file" ]]; then
    warn "Skipping (not found): $file"
    return
  fi
  if grep -q "$OLD_IP" "$file"; then
    sed -i "s|${OLD_IP}|${NEW_IP}|g" "$file"
    local count; count=$(grep -c "$NEW_IP" "$file")
    ok "Updated $file ($count occurrence(s))"
  else
    info "No match in $file — skipping"
  fi
}

# ── 1. Update project files ────────────────────────────────────────────────
section "Step 1 / 3 — Updating project files"
replace_in_file "$APP_DIR/generate_ssl.sh"
replace_in_file "$APP_DIR/startup-mobile.sh"
replace_in_file "$APP_DIR/mobile/app.json"
replace_in_file "$APP_DIR/mobile/stores/useAppStore.ts"

# ── 2. Regenerate SSL certificate ─────────────────────────────────────────
section "Step 2 / 3 — Regenerating SSL certificate"
bash "$APP_DIR/generate_ssl.sh"

# ── 3. Reload nginx ───────────────────────────────────────────────────────
section "Step 3 / 3 — Reloading nginx"
if systemctl is-active nginx &>/dev/null; then
  nginx -t && systemctl reload nginx
  ok "Nginx reloaded with new certificate"
else
  warn "Nginx is not running — start it with: sudo systemctl start nginx"
fi

# ── Summary ───────────────────────────────────────────────────────────────
PRIVATE_IP=$(hostname -I | awk '{print $1}')
echo ""
echo -e "${BOLD}${GRN}  Migration complete: $OLD_IP → $NEW_IP${R}"
echo ""
echo -e "  ${BOLD}Streamlit   :${R}  http://$NEW_IP:8501"
echo -e "  ${BOLD}API (HTTPS) :${R}  https://$NEW_IP/api/status"
echo -e "  ${BOLD}Expo Go URL :${R}  exp://$NEW_IP:8081"
echo ""
echo -e "  ${YLW}Laptop:${R} Update mobile/app.json and mobile/stores/useAppStore.ts"
echo -e "         Replace ${BOLD}$OLD_IP${R} → ${BOLD}$NEW_IP${R} in both files."
echo ""
echo -e "  ${YLW}Android SSL:${R} Re-install /etc/ssl/oracle-dba/server.crt on device"
echo -e "         Fingerprint: $(openssl x509 -noout -fingerprint -sha256 \
         -in /etc/ssl/oracle-dba/server.crt 2>/dev/null | cut -d= -f2)"
echo ""
