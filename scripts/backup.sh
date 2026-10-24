#!/usr/bin/env bash
# Back up PostgreSQL, MinIO objects, and (optionally) the Chroma volume.
#
# Usage:
#   scripts/backup.sh [destination]
#   BACKUP_CHROMA=1 scripts/backup.sh
#   BACKUP_RETENTION_DAYS=14 scripts/backup.sh
#
# Run on the production Docker host. The compose stack must be running.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose -f "$ROOT/docker-compose.prod.yml")
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose -f "$ROOT/docker-compose.prod.yml")
else
  echo "docker compose (plugin) or docker-compose is required" >&2
  exit 1
fi
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${1:-$ROOT/backups/$STAMP}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
BACKUP_CHROMA="${BACKUP_CHROMA:-0}"
CHROMA_VOLUME="${CHROMA_VOLUME:-much-agents-prod_chroma-data}"

if [[ ! -f "$ROOT/deploy/env.production" ]]; then
  echo "deploy/env.production is missing" >&2
  exit 1
fi
if [[ ! -f "$ROOT/deploy/nginx/certs/fullchain.pem" ]]; then
  echo "warning: TLS certificate is missing; it is NOT included in backups" >&2
fi

mkdir -p "$DEST/minio"
# Docker bind mounts require an absolute path even if the user passed a
# relative destination.
DEST="$(cd "$DEST" && pwd)"
echo "Backing up to $DEST"

echo "[1/3] PostgreSQL dump"
"${COMPOSE[@]}" exec -T postgres sh -c \
  'pg_dump --format=custom --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  > "$DEST/postgres.dump"
if [[ ! -s "$DEST/postgres.dump" ]]; then
  echo "PostgreSQL dump is empty" >&2
  exit 1
fi

echo "[2/3] MinIO objects"
"${COMPOSE[@]}" run --rm --no-deps \
  --entrypoint sh \
  -v "$DEST/minio:/backup" \
  createbuckets -c '
    set -eu
    mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
    mc mirror --overwrite "local/$MINIO_BUCKET" /backup
  '

if [[ "$BACKUP_CHROMA" == "1" ]]; then
  echo "[3/3] Chroma volume (optional)"
  mkdir -p "$DEST/chroma"
  docker run --rm \
    -v "$CHROMA_VOLUME:/data:ro" \
    -v "$DEST/chroma:/backup" \
    alpine:3.20 \
    sh -c 'tar -czf /backup/chroma.tgz -C /data .'
else
  echo "[3/3] Chroma skipped (rebuildable from parsed artifacts; set BACKUP_CHROMA=1 to include)"
fi

(
  cd "$DEST"
  find . -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum > SHA256SUMS
)

echo
echo "Backup complete: $DEST"
echo "Verify with: (cd '$DEST' && sha256sum -c SHA256SUMS)"
echo "IMPORTANT: store deploy/env.production in a separate encrypted secret store;"
echo "it is intentionally not copied into this backup."

if [[ -d "$ROOT/backups" ]]; then
  find "$ROOT/backups" -mindepth 1 -maxdepth 1 -type d \
    -mtime "+$RETENTION_DAYS" -print -exec rm -rf -- {} + || true
fi
