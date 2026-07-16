#!/usr/bin/env bash
# =============================================================================
# OmniDBA v3 — public UI access toggle
# =============================================================================
# Flips the `allow-omnidba-ui` firewall rule (tcp:8610) on/off so you control
# exactly when the demo UI is reachable from the internet.
#
#   ./ops/ui-access.sh open     Open  — team can reach http://<ip>:8610
#   ./ops/ui-access.sh close    Close — firewall blocks 8610 (default when idle)
#   ./ops/ui-access.sh status   Show current state + shareable link
#
# ⚠️  While OPEN the UI is HTTP + NO login and runs on your ADC. Close it when
#     you're not actively sharing.
# =============================================================================
set -euo pipefail

PROJECT="${GCP_PROJECT:-in-26301-bell-poc}"
RULE="allow-omnidba-ui"
PORT="8610"
EXTERNAL_IP="34.14.171.170"
API="https://compute.googleapis.com/compute/v1/projects/${PROJECT}/global/firewalls/${RULE}"

token() { gcloud auth application-default print-access-token 2>/dev/null; }

get_disabled() {
  curl -s -H "Authorization: Bearer $(token)" "$API" \
    | python3 -c "import sys,json;print(str(json.load(sys.stdin).get('disabled', False)).lower())"
}

set_disabled() {  # $1 = true|false
  local code
  code=$(curl -s -o /tmp/ui_access.json -w "%{http_code}" -X PATCH \
    -H "Authorization: Bearer $(token)" -H "Content-Type: application/json" \
    "$API" -d "{\"disabled\": $1}")
  if [[ "$code" != "200" ]]; then
    echo "  ✗ API error ($code): $(cat /tmp/ui_access.json)"; exit 1
  fi
}

case "${1:-status}" in
  open)
    set_disabled false
    echo "  ✔ UI OPEN — share this link with your team:"
    echo "      http://${EXTERNAL_IP}:${PORT}"
    echo "  (remember: no login, HTTP — run './ops/ui-access.sh close' when done)"
    ;;
  close)
    set_disabled true
    echo "  ✔ UI CLOSED — firewall now blocks :${PORT} from the internet."
    ;;
  status)
    if [[ "$(get_disabled)" == "true" ]]; then
      echo "  ● CLOSED — :${PORT} blocked. Run 'open' to share."
    else
      echo "  ● OPEN — reachable at http://${EXTERNAL_IP}:${PORT}"
    fi
    ;;
  *) echo "usage: $0 {open|close|status}"; exit 2 ;;
esac
