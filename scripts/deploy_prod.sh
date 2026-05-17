#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.prod.yml}
ENV_FILE=${ENV_FILE:-.env.production}

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[deploy] ERROR: $ENV_FILE not found. Copy .env.production.example first." >&2
  exit 1
fi

echo "[deploy] Updating git checkout"
git pull --ff-only

echo "[deploy] Building and starting production services"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --build

echo "[deploy] Applying migrations"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec backend alembic upgrade head

echo "[deploy] Checking backend health"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec backend \
  sh -c 'curl -fsS "http://localhost:${BACKEND_PORT:-8000}/api/v1/health"'

echo "[deploy] Deployment completed"
