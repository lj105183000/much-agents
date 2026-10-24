#!/usr/bin/env bash
# Restore a backup produced by scripts/backup.sh.
#
# Usage:
#   scripts/restore.sh backups/20261024T120000Z
#   FORCE=1 scripts/restore.sh <dir>          # non-interactive (automation)
#   RESTORE_CHROMA=1 scripts/restore.sh <dir>
#
# This replaces database contents and MinIO objects. API and workers are
# stopped during the restore and started again at the end.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose -f "$ROOT/docker-compose.prod.yml")
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose -f "$ROOT/docker-compose.prod.yml")
else
  echo "docker compose (plugin) or docker-compose is required" >&2
  exit 1
fi
SOURCE="${1:-}"
FORCE="${FORCE:-0}"
RESTORE_CHROMA="${RESTORE_CHROMA:-0}"
CHROMA_VOLUME="${CHROMA_VOLUME:-much-agents-prod_chroma-data}"

if [[ -z "$SOURCE" || ! -d "$SOURCE" ]]; then
  echo "usage: scripts/restore.sh <backup-directory>" >&2
  exit 1
fi
if [[ ! -f "$SOURCE/postgres.dump" ]]; then
  echo "$SOURCE/postgres.dump is missing" >&2
  exit 1
fi
# Docker bind mounts require an absolute path even if the user passed a
# relative backup directory.
SOURCE="$(cd "$SOURCE" && pwd)"

echo "Verifying backup checksums..."
(cd "$SOURCE" && if [[ -f SHA256SUMS ]]; then sha256sum -c SHA256SUMS; else echo "SHA256SUMS missing; skipping"; fi)

if [[ "$FORCE" != "1" ]]; then
  echo
  echo "This will REPLACE the production database and MinIO objects with:"
  echo "  $SOURCE"
  read -r -p "Type RESTORE to continue: " answer
  if [[ "$answer" != "RESTORE" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

echo "[1/5] Stopping API and workers"
"${COMPOSE[@]}" stop api worker-parse worker-bid

restore_minio() {
  echo "[2/5] Restoring MinIO objects"
  "${COMPOSE[@]}" run --rm --no-deps \
    --entrypoint sh \
    -v "$SOURCE/minio:/backup:ro" \
    createbuckets -c '
      set -eu
      mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
      mc mirror --overwrite --remove /backup "local/$MINIO_BUCKET"
    '
}
restore_minio

echo "[3/5] Restoring PostgreSQL"
"${COMPOSE[@]}" exec -T postgres sh -c \
  'pg_restore --clean --if-exists --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < "$SOURCE/postgres.dump"

if [[ "$RESTORE_CHROMA" == "1" && -f "$SOURCE/chroma/chroma.tgz" ]]; then
  echo "[4/5] Restoring Chroma volume (optional)"
  docker run --rm \
    -v "$CHROMA_VOLUME:/data" \
    -v "$SOURCE/chroma:/backup:ro" \
    alpine:3.20 \
    sh -c 'rm -rf /data/* && tar -xzf /backup/chroma.tgz -C /data'
else
  echo "[4/5] Chroma restore skipped (optional; it can be rebuilt from parsed artifacts)"
fi

echo "[5/5] Starting API and workers"
"${COMPOSE[@]}" up -d api worker-parse worker-bid

echo
echo "Database row counts:"
"${COMPOSE[@]}" exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT 'users' AS table_name, count(*) FROM users
   UNION ALL SELECT 'projects', count(*) FROM projects
   UNION ALL SELECT 'bid_runs', count(*) FROM bid_runs
   UNION ALL SELECT 'audit_events', count(*) FROM audit_events;"

echo "MinIO object count:"
"${COMPOSE[@]}" run --rm --no-deps --entrypoint sh createbuckets -c '
  mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
  mc ls --recursive "local/$MINIO_BUCKET" | wc -l
'

echo
echo "Restore finished. Verify login, open a bid run, download an artifact,"
echo "and run GET /api/ready before re-enabling external access."
