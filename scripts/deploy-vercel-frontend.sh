#!/usr/bin/env bash
# Deploy frontend to Vercel (requires: npm, vercel CLI or npx).
#
# Usage:
#   export NEXT_PUBLIC_API_URL=https://aie-api.<region>.azurecontainerapps.io
#   ./scripts/deploy-vercel-frontend.sh

set -euo pipefail

NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:?Set NEXT_PUBLIC_API_URL to your Azure API URL}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cd "$REPO_ROOT/frontend"
export NEXT_PUBLIC_API_URL

if command -v vercel >/dev/null 2>&1; then
  vercel deploy --prod --yes
else
  npx vercel@latest deploy --prod --yes
fi

echo "Deployed. Ensure NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL is set in Vercel project settings."
