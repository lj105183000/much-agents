#!/usr/bin/env bash
# Restore a backup produced by scripts/backup.sh.
#
# Usage:
#   scripts/restore.sh backups/20261024T120000Z
#   FORCE=1 scripts/restore.sh <dir>          # non-interactive (automation)
#   RESTORE_CHROMA=1 scripts/restore.sh <dir>
#   ALLOW_MISSING_CHECKSUMS=1 scripts/restore.sh <dir>  # no SHA256SUMS file
#   ALLOW_EMPTY_MINIO=1 scripts/restore.sh <dir>        # intentionally empty bucket
#
# This replaces database contents and MinIO objects. API and workers are
# stopped during the restore and started again at the end. On failure they
# stay stopped; the script never exposes a partially restored database.
set -Eeuo pipefail

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
ALLOW_MISSING_CHECKSUMS="${ALLOW_MISSING_CHECKSUMS:-0}"
ALLOW_EMPTY_MINIO="${ALLOW_EMPTY_MINIO:-0}"
CHROMA_VOLUME="${CHROMA_VOLUME:-much-agents-prod_chroma-data}"
STOPPED=0

restore_failed() {
  status=$?
  echo >&2
  echo "Restore failed (exit $status)." >&2
  if [[ "$STOPPED" == "1" ]]; then
    echo "API/workers are stopped and MinIO may already have been restored." >&2
    echo "Do not start API/workers against a partially restored database." >&2
  else
    echo "API/workers were not stopped yet." >&2
  fi
  echo "Fix the error, then rerun: FORCE=1 scripts/restore.sh <dir>" >&2
  exit "$status"
}
trap restore_failed ERR

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

if [[ ! -f "$SOURCE/SHA256SUMS" ]]; then
  if [[ "$ALLOW_MISSING_CHECKSUMS" == "1" ]]; then
    echo "warning: SHA256SUMS missing; skipping checksum verification" >&2
  else
    echo "$SOURCE/SHA256SUMS is missing; refusing to restore an unverified backup" >&2
    echo "Set ALLOW_MISSING_CHECKSUMS=1 only if you trust this backup." >&2
    exit 1
  fi
fi

if [[ ! -d "$SOURCE/minio" ]]; then
  if [[ "$ALLOW_EMPTY_MINIO" == "1" ]]; then
    echo "warning: $SOURCE/minio is missing; treating the bucket as empty" >&2
    mkdir -p "$SOURCE/minio"
  else
    echo "$SOURCE/minio is missing; refusing to wipe the bucket" >&2
    echo "Set ALLOW_EMPTY_MINIO=1 only if the bucket is intentionally empty." >&2
    exit 1
  fi
elif [[ -z "$(find "$SOURCE/minio" -type f -print -quit)" ]]; then
  if [[ "$ALLOW_EMPTY_MINIO" == "1" ]]; then
    echo "warning: $SOURCE/minio is empty; treating the bucket as empty" >&2
  else
    echo "$SOURCE/minio is empty; refusing to wipe the bucket" >&2
    echo "Set ALLOW_EMPTY_MINIO=1 only if the bucket is intentionally empty." >&2
    exit 1
  fi
fi

if [[ -f "$SOURCE/SHA256SUMS" ]]; then
  echo "Verifying backup checksums..."
  # Keep this out of the ERR trap so a bad checksum reports once.
  if ! (cd "$SOURCE" && sha256sum -c SHA256SUMS); then
    echo "Checksum verification failed; refusing to restore" >&2
    exit 1
  fi
fi

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
STOPPED=1

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
  'pg_restore --clean --if-exists --exit-on-error --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
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
