#!/usr/bin/env bash
# Full local Postgres → Azure Postgres migration (preserves analysis rows).
#
# After restore, sets ANALYZE_FROM so the worker only LLM-analyzes filings
# ingested from this point forward (exported pending backlog is skipped).
#
# Prerequisites: docker (local aie_postgres), az login, .deploy/azure.env
#
# Usage:
#   ./scripts/migrate-local-to-azure.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=/dev/null
source "${REPO_ROOT}/.deploy/azure.env"

MIGRATE_FROM="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PUBLIC_IP="$(curl -4 -s ifconfig.me)"
HOST="${DB_SERVER}.postgres.database.azure.com"
DUMP="/tmp/aie_restore.sql"

echo "==> Migrate from local Docker Postgres → Azure ($HOST)"
echo "    ANALYZE_FROM will be set to: $MIGRATE_FROM"

docker ps --filter name=aie_postgres --format '{{.Status}}' | grep -q Up || \
  (cd "$REPO_ROOT" && docker compose up -d db && sleep 3)

echo "==> Firewall rule for $PUBLIC_IP"
az postgres flexible-server firewall-rule create -g "$RESOURCE_GROUP" -s "$DB_SERVER" \
  -n AllowDevRestore --start-ip-address "$PUBLIC_IP" --end-ip-address "$PUBLIC_IP" -o none 2>/dev/null || true

echo "==> Dump local database"
docker exec aie_postgres pg_dump -U aie -d aie --no-owner --no-acl --clean --if-exists \
  | grep -v '^\\restrict' | grep -v '^\\unrestrict' > "$DUMP"

echo "==> Restore to Azure"
export PGPASSWORD="$DB_ADMIN_PASSWORD"
export PGSSLMODE=require
docker run --rm -i -e PGPASSWORD -e PGSSLMODE=require postgres:16 \
  psql -h "$HOST" -U "$DB_ADMIN_USER" -d "$DB_NAME" -v ON_ERROR_STOP=0 < "$DUMP" | tail -5

echo "==> Skip exported pending backlog (no LLM on pre-migration queue)"
docker run --rm -e PGPASSWORD -e PGSSLMODE=require postgres:16 \
  psql -h "$HOST" -U "$DB_ADMIN_USER" -d "$DB_NAME" -c "
UPDATE raw_announcements
SET analysis_status = 'skipped', skip_reason = 'historical_backfill'
WHERE analysis_status IN ('pending', 'processing')
  AND fetched_at < timestamptz '$MIGRATE_FROM';
"

echo "==> Update Container Apps ANALYZE_FROM"
az containerapp update -g "$RESOURCE_GROUP" -n "$WORKER_APP" \
  --set-env-vars "ANALYZE_FROM=$MIGRATE_FROM" "ANALYZE_BACKFILL=false" -o none
az containerapp update -g "$RESOURCE_GROUP" -n "$API_APP" \
  --set-env-vars "ANALYZE_FROM=$MIGRATE_FROM" "ANALYZE_BACKFILL=false" -o none

az postgres flexible-server firewall-rule delete -g "$RESOURCE_GROUP" -s "$DB_SERVER" \
  -n AllowDevRestore --yes -o none 2>/dev/null || true

echo "==> Done. Verify: curl \$API_URL/api/stats"
