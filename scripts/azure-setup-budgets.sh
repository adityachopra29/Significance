#!/usr/bin/env bash
# Create Azure Cost Management budgets for subscription + aie-rg project.
#
# Prerequisites: az login, Cost Management permissions on subscription.
#
# Usage:
#   export MONTHLY_BUDGET_USD=50          # subscription alert threshold
#   export PROJECT_BUDGET_USD=45          # aie-rg only
#   export ALERT_EMAIL=you@example.com
#   ./scripts/azure-setup-budgets.sh
#
# Note: Pay-as-you-go has NO hard spending cap. Budgets send alerts only unless
# you wire an Action Group to automation (runbook) to stop resources.

set -euo pipefail

SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-$(az account show --query id -o tsv)}"
RESOURCE_GROUP="${RESOURCE_GROUP:-aie-rg}"
MONTHLY_BUDGET_USD="${MONTHLY_BUDGET_USD:-50}"
PROJECT_BUDGET_USD="${PROJECT_BUDGET_USD:-45}"
ALERT_EMAIL="${ALERT_EMAIL:?Set ALERT_EMAIL}"

START=$(date -u +%Y-%m-01)
END=$(date -u -v+5y +%Y-%m-01 2>/dev/null || date -u -d '+5 years' +%Y-%m-01)

echo "==> Subscription budget: \$$MONTHLY_BUDGET_USD/month (alerts at 50%, 80%, 100%)"
for pct in 50 80 100; do
  amount=$(python3 -c "print(round($MONTHLY_BUDGET_USD * $pct / 100, 2))")
  az consumption budget create \
    --budget-name "aie-subscription-${pct}pct" \
    --category cost \
    --amount "$amount" \
    --time-grain monthly \
    --start-date "$START" \
    --end-date "$END" \
    --subscription "$SUBSCRIPTION_ID" \
    -o none 2>/dev/null || echo "  (budget aie-subscription-${pct}pct may already exist — update in portal)"
done

echo "==> Project budget (resource group $RESOURCE_GROUP): \$$PROJECT_BUDGET_USD/month"
az consumption budget create \
  --budget-name "aie-rg-monthly" \
  --category cost \
  --amount "$PROJECT_BUDGET_USD" \
  --time-grain monthly \
  --start-date "$START" \
  --end-date "$END" \
  --resource-group-filter "$RESOURCE_GROUP" \
  --subscription "$SUBSCRIPTION_ID" \
  -o none 2>/dev/null || echo "  (budget aie-rg-monthly may already exist — update in portal)"

cat <<EOF

Budget CLI records created (or already present).

IMPORTANT — attach email alerts in Azure Portal:
  1. Cost Management + Billing → Budgets
  2. Open each budget → Alert conditions → Add
  3. Thresholds: 50%, 80%, 100% of budget (Actual cost)
  4. Action group → create with email: $ALERT_EMAIL

For project-only view:
  Cost Management → Cost analysis → Scope: $RESOURCE_GROUP

Pay-as-you-go subscriptions cannot auto-stop at a dollar cap.
To auto-shutdown on overrun, link budget alerts to an Automation runbook:
  https://learn.microsoft.com/azure/cost-management-billing/costs/cost-mgt-alerts-monitor-usage-spending

EOF
