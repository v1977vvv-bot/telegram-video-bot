#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.prod.yml}
ENV_FILE=${ENV_FILE:-.env.production}
BACKUP_DIR=${BACKUP_DIR:-backups}

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[backup] ERROR: $ENV_FILE not found." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
timestamp=$(date -u +"%Y%m%dT%H%M%SZ")
backup_file="$BACKUP_DIR/postgres_${timestamp}.dump"

echo "[backup] Writing $backup_file"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup_file"

echo "[backup] Done"
