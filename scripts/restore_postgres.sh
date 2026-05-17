#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.prod.yml}
ENV_FILE=${ENV_FILE:-.env.production}

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 backups/postgres_YYYYMMDDTHHMMSSZ.dump [--yes]" >&2
  exit 1
fi

backup_file=$1
confirm=${2:-}

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[restore] ERROR: $ENV_FILE not found." >&2
  exit 1
fi

if [[ ! -f "$backup_file" ]]; then
  echo "[restore] ERROR: backup file not found: $backup_file" >&2
  exit 1
fi

if [[ "$confirm" != "--yes" ]]; then
  echo "[restore] WARNING: this will overwrite the production database."
  read -r -p "Type RESTORE to continue: " answer
  if [[ "$answer" != "RESTORE" ]]; then
    echo "[restore] Cancelled"
    exit 1
  fi
fi

echo "[restore] Restoring $backup_file"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T postgres \
  sh -c 'pg_restore --clean --if-exists -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$backup_file"

echo "[restore] Done"
