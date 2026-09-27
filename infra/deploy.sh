#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.prod.yml"

echo "==> preflight: required env"
grep -qE '^DATABASE_URL=' ../.env || { echo "FAIL: .env missing DATABASE_URL (see .env.example)"; exit 1; }
grep -qE '^REDIS_URL=' ../.env || { echo "FAIL: .env missing REDIS_URL (see .env.example)"; exit 1; }

echo "==> build"
$COMPOSE build

echo "==> bring up data layer"
$COMPOSE up -d postgres redis

echo "==> database: schema + migrations"
# NOTE: fixes a bug in an earlier draft of this script, which ran `psql`
# inside the api/node image — that image has no psql client installed.
# The postgres service image does, so schema setup runs there instead.
$COMPOSE exec -T postgres psql -U "${PG_USER:-compos}" -d compos \
  -c 'CREATE SCHEMA IF NOT EXISTS brain' \
  -c 'CREATE EXTENSION IF NOT EXISTS vector'

$COMPOSE run --rm api sh -c "cd /app/packages/db && pnpm db:migrate"
$COMPOSE run --rm brain sh -c "cd /app/packages/brain && pnpm db:migrate"

echo "==> up"
$COMPOSE up -d

echo "==> smoke"
sleep 10
curl -fsS "http://localhost/healthz" >/dev/null 2>&1 || echo "WARN: api healthz not reachable via ingress yet (DNS/TLS still provisioning?)"
$COMPOSE exec -T brain wget -qO- http://localhost:3010/healthz || { echo "FAIL: brain heartbeat not responding"; exit 1; }

echo "==> deployed."
