#!/usr/bin/env bash
# Generate a self-signed TLS certificate for an internal Much Agents host.
set -euo pipefail

HOST="${1:-localhost}"
DAYS="${TLS_DAYS:-825}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/certs"
KEY="$DIR/privkey.pem"
CRT="$DIR/fullchain.pem"

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required" >&2
  exit 1
fi

if [[ -f "$KEY" || -f "$CRT" ]]; then
  echo "Refusing to overwrite existing $KEY or $CRT" >&2
  echo "Move them away first if you really want to regenerate." >&2
  exit 1
fi

SAN="DNS:$HOST"
if [[ "$HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  SAN="IP:$HOST"
fi

SUBJ="/CN=$HOST"
# Git Bash rewrites a leading "/" in native arguments into a Windows path.
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
  SUBJ="//CN=$HOST"
fi

mkdir -p "$DIR"
openssl req -x509 -newkey rsa:3072 -sha256 -days "$DAYS" -nodes \
  -keyout "$KEY" \
  -out "$CRT" \
  -subj "$SUBJ" \
  -addext "subjectAltName=$SAN" \
  -addext "keyUsage=digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth"

chmod 600 "$KEY"
chmod 644 "$CRT"
echo "Wrote $CRT and $KEY for $HOST (valid $DAYS days)."
echo "Import fullchain.pem into the client trust store before using the UI."
