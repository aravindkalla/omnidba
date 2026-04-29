#!/usr/bin/env bash
# =============================================================================
# Generate a self-signed TLS certificate for the OmniDBA nginx proxy.
#
# What this creates:
#   /etc/ssl/oracle-dba/server.key  — RSA-2048 private key
#   /etc/ssl/oracle-dba/server.crt  — Self-signed X.509 cert (valid 10 years)
#
# USAGE:
#   sudo ./generate_ssl.sh
#
# After running, install the .crt on each mobile device:
#   Android : copy to device → Settings → Security → Install certificate
#   iOS     : AirDrop/email the .crt → tap it → Settings → General →
#             VPN & Device Management → trust it
#
# The cert uses your server's IP as the Subject Alternative Name (SAN) so
# that modern browsers and mobile clients accept it without SNI errors.
# =============================================================================
set -euo pipefail

SSL_DIR="/etc/ssl/oracle-dba"
KEY="$SSL_DIR/server.key"
CRT="$SSL_DIR/server.crt"
DAYS=3650   # 10-year validity for an internal tool

if [[ $EUID -ne 0 ]]; then
  echo "[ERROR] Run with sudo: sudo ./generate_ssl.sh" >&2
  exit 1
fi

# Use the public IP (GCP external IP — update this when the VM IP changes)
SERVER_IP="34.14.171.170"
echo "[INFO] Using server public IP: $SERVER_IP"

mkdir -p "$SSL_DIR"
chmod 700 "$SSL_DIR"

# Generate key + cert in one step with a SAN extension
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$KEY" \
  -out    "$CRT" \
  -days   "$DAYS" \
  -subj   "/CN=OmniDBA/O=CGI/OU=DBA" \
  -extensions v3_req \
  -addext "subjectAltName=IP:$SERVER_IP,IP:10.160.0.2,IP:127.0.0.1" 2>/dev/null

chmod 600 "$KEY"
chmod 644 "$CRT"

echo ""
echo "[OK] Certificate generated:"
echo "     Key : $KEY"
echo "     Cert: $CRT"
echo ""
echo "[INFO] Certificate fingerprint (SHA-256):"
openssl x509 -noout -fingerprint -sha256 -in "$CRT"
echo ""
echo "[NEXT] Install $CRT on each mobile device to trust this server."
echo "       Android : transfer file → Settings → Security → Install certificate"
echo "       iOS     : AirDrop or email → tap file → Settings → General → VPN & Device Management"
echo ""
echo "[NEXT] Then run:  sudo nginx -t && sudo systemctl reload nginx"
