#!/usr/bin/env bash
# Fallback when cert-manager is not installed: create the wisecow-tls secret from a self-signed cert.
set -euo pipefail
HOST="${1:-wisecow.local}"
NS="${2:-wisecow}"
TMP="$(mktemp -d)"
openssl req -x509 -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1   -days 365 -keyout "$TMP/tls.key" -out "$TMP/tls.crt"   -subj "/CN=${HOST}/O=wisecow" -addext "subjectAltName=DNS:${HOST}"
kubectl -n "$NS" create secret tls wisecow-tls --cert="$TMP/tls.crt" --key="$TMP/tls.key"   --dry-run=client -o yaml | kubectl apply -f -
rm -rf "$TMP"
echo "TLS secret wisecow-tls created in namespace $NS for host $HOST"
