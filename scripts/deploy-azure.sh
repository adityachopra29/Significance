#!/usr/bin/env bash
# Deploy AIE backend to Azure Container Apps + PostgreSQL Flexible Server.
#
# Prerequisites:
#   brew install azure-cli docker
#   az login
#   az account set --subscription "<subscription-id>"
#
# Usage:
#   export RESOURCE_GROUP=aie-rg LOCATION=centralindia ACR_NAME=aieacr$RANDOM
#   export DB_ADMIN_PASSWORD='...' LLM_API_KEY='...' CORS_ORIGINS='https://your-app.vercel.app'
#   ./scripts/deploy-azure.sh
#
# Optional: NSE_PROXY_URL, LLM_PROVIDER (default openai), LLM_MODEL (default gpt-4o-mini)

set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-aie-rg}"
LOCATION="${LOCATION:-centralindia}"
ACR_NAME="${ACR_NAME:-aieacr$(openssl rand -hex 3)}"
ENV_NAME="${ENV_NAME:-aie-env}"
API_APP="${API_APP:-aie-api}"
WORKER_APP="${WORKER_APP:-aie-worker}"
DB_SERVER="${DB_SERVER:-aie-pg-$(openssl rand -hex 3)}"
DB_NAME="${DB_NAME:-aie}"
DB_ADMIN_USER="${DB_ADMIN_USER:-aieadmin}"
DB_ADMIN_PASSWORD="${DB_ADMIN_PASSWORD:?Set DB_ADMIN_PASSWORD}"
LLM_API_KEY="${LLM_API_KEY:?Set LLM_API_KEY}"
LLM_PROVIDER="${LLM_PROVIDER:-openai}"
LLM_MODEL="${LLM_MODEL:-gpt-4o-mini}"
CORS_ORIGINS="${CORS_ORIGINS:-http://localhost:3000}"
API_MIN_REPLICAS="${API_MIN_REPLICAS:-0}"
API_MAX_REPLICAS="${API_MAX_REPLICAS:-1}"
API_CPU="${API_CPU:-0.25}"
API_MEMORY="${API_MEMORY:-0.5Gi}"
WORKER_MIN_REPLICAS="${WORKER_MIN_REPLICAS:-1}"
WORKER_MAX_REPLICAS="${WORKER_MAX_REPLICAS:-1}"
WORKER_CPU="${WORKER_CPU:-0.25}"
WORKER_MEMORY="${WORKER_MEMORY:-0.5Gi}"
PG_SKU="${PG_SKU:-Standard_B1ms}"
PG_STORAGE_GB="${PG_STORAGE_GB:-32}"

echo "==> Resource group: $RESOURCE_GROUP ($LOCATION)"
az group create -n "$RESOURCE_GROUP" -l "$LOCATION" --tags project=significance environment=production cost-center=aie -o none

echo "==> Container registry: $ACR_NAME"
az acr create -g "$RESOURCE_GROUP" -n "$ACR_NAME" --sku Basic -o none
az acr login -n "$ACR_NAME"

IMAGE="$ACR_NAME.azurecr.io/aie-backend:$IMAGE_TAG"
echo "==> Building and pushing $IMAGE"
docker build -t "$IMAGE" "$REPO_ROOT/backend"
docker push "$IMAGE"

echo "==> PostgreSQL flexible server: $DB_SERVER"
az postgres flexible-server create \
  -g "$RESOURCE_GROUP" \
  -n "$DB_SERVER" \
  -l "$LOCATION" \
  --tier Burstable \
  --sku-name "$PG_SKU" \
  --storage-size "$PG_STORAGE_GB" \
  --version 16 \
  --admin-user "$DB_ADMIN_USER" \
  --admin-password "$DB_ADMIN_PASSWORD" \
  --public-access 0.0.0.0 \
  -o none

az postgres flexible-server db create \
  -g "$RESOURCE_GROUP" \
  -s "$DB_SERVER" \
  --database-name "$DB_NAME" \
  -o none

DB_HOST="${DB_SERVER}.postgres.database.azure.com"
DATABASE_URL="postgresql+psycopg2://${DB_ADMIN_USER}:${DB_ADMIN_PASSWORD}@${DB_HOST}:5432/${DB_NAME}?sslmode=require"

echo "==> Container Apps environment: $ENV_NAME"
az containerapp env create -g "$RESOURCE_GROUP" -n "$ENV_NAME" -l "$LOCATION" -o none

ACR_PASS=$(az acr credential show -n "$ACR_NAME" --query "passwords[0].value" -o tsv)
ACR_USER=$(az acr credential show -n "$ACR_NAME" --query "username" -o tsv)

common_env=(
  "DATABASE_URL=$DATABASE_URL"
  "LLM_PROVIDER=$LLM_PROVIDER"
  "LLM_MODEL=$LLM_MODEL"
  "LLM_API_KEY=$LLM_API_KEY"
  "PURGE_ENABLED=false"
  "NSE_INGEST_ENABLED=true"
  "BACKFILL_DAYS=7"
  "ANALYZE_BACKFILL=false"
  "ANALYZE_FROM=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  "POLL_INTERVAL_SECONDS=60"
)

echo "==> API container app: $API_APP"
az containerapp create \
  -g "$RESOURCE_GROUP" \
  -n "$API_APP" \
  --environment "$ENV_NAME" \
  --image "$IMAGE" \
  --registry-server "${ACR_NAME}.azurecr.io" \
  --registry-username "$ACR_USER" \
  --registry-password "$ACR_PASS" \
  --target-port 8000 \
  --ingress external \
  --min-replicas "$API_MIN_REPLICAS" \
  --max-replicas "$API_MAX_REPLICAS" \
  --cpu "$API_CPU" \
  --memory "$API_MEMORY" \
  --env-vars "${common_env[@]}" "CORS_ORIGINS=$CORS_ORIGINS" "INGEST_ON_STARTUP=false" \
  -o none

API_URL="https://$(az containerapp show -g "$RESOURCE_GROUP" -n "$API_APP" --query properties.configuration.ingress.fqdn -o tsv)"
echo "    API URL: $API_URL"

worker_env=("${common_env[@]}" "INGEST_ON_STARTUP=true" "CORS_ORIGINS=$CORS_ORIGINS")
if [[ -n "${NSE_PROXY_URL:-}" ]]; then
  worker_env+=("NSE_PROXY_URL=$NSE_PROXY_URL")
fi

echo "==> Worker container app: $WORKER_APP"
# Azure CLI splits --command incorrectly if passed as separate tokens; use YAML or
# a single command array. Example one-liner after create if args are wrong:
#   az containerapp show -g RG -n WORKER -o yaml | edit command to [python,-m,app.run_worker]
az containerapp create \
  -g "$RESOURCE_GROUP" \
  -n "$WORKER_APP" \
  --environment "$ENV_NAME" \
  --image "$IMAGE" \
  --registry-server "${ACR_NAME}.azurecr.io" \
  --registry-username "$ACR_USER" \
  --registry-password "$ACR_PASS" \
  --ingress internal \
  --min-replicas "$WORKER_MIN_REPLICAS" \
  --max-replicas "$WORKER_MAX_REPLICAS" \
  --cpu "$WORKER_CPU" \
  --memory "$WORKER_MEMORY" \
  --args "-m" "app.run_worker" \
  --command "python" \
  --env-vars "${worker_env[@]}" \
  -o none

echo ""
echo "=============================================="
echo "Azure backend deployed."
echo "  API:  $API_URL"
echo "  Health: $API_URL/health"
echo ""
echo "Next steps:"
echo "  1. Set Vercel env: NEXT_PUBLIC_API_URL=$API_URL"
echo "  2. Update API CORS if needed: CORS_ORIGINS=<vercel-url>"
echo "  3. Bootstrap DB (one-off on worker):"
echo "       az containerapp exec -g $RESOURCE_GROUP -n $WORKER_APP -- python -m app.scripts.load_universe --mode merged"
echo "       az containerapp exec -g $RESOURCE_GROUP -n $WORKER_APP -- python -m app.scripts.run_ingest_backfill --days 90"
echo "     Or restore pg_dump from local."
echo "  4. Test NSE: az containerapp exec ... -- python -m app.scripts.test_nse_source --days 1"
echo "=============================================="
