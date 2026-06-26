#!/usr/bin/env bash
#
# guardrails.sh — one-time cost safety setup. Run this ONCE after configuring creds.
#
#   create-budget <USD>   Create an AWS Budget that emails you at 80%/100% of <USD>/month.
#   orphans               List any lab resources still alive (tagged project=awslabs).
#   nuke                  Destroy ALL lab resources by tag (last-resort cleanup).
#
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
preflight

cmd="${1:-help}"; shift || true

case "$cmd" in
  create-budget)
    limit="${1:-5}"
    email="$(aws sts get-caller-identity --query Account --output text >/dev/null; echo "${BUDGET_EMAIL:-}")"
    if [ -z "$email" ]; then
      read -r -p "Email for budget alerts: " email
    fi
    cat > /tmp/budget.json <<JSON
{ "BudgetName": "awslabs-monthly-cap",
  "BudgetLimit": { "Amount": "${limit}", "Unit": "USD" },
  "TimeUnit": "MONTHLY", "BudgetType": "COST" }
JSON
    cat > /tmp/notifications.json <<JSON
[ { "Notification": { "NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN", "Threshold": 80, "ThresholdType": "PERCENTAGE" },
    "Subscribers": [ { "SubscriptionType": "EMAIL", "Address": "${email}" } ] },
  { "Notification": { "NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN", "Threshold": 100, "ThresholdType": "PERCENTAGE" },
    "Subscribers": [ { "SubscriptionType": "EMAIL", "Address": "${email}" } ] } ]
JSON
    aws budgets create-budget \
      --account-id "$ACCOUNT_ID" \
      --budget file:///tmp/budget.json \
      --notifications-with-subscribers file:///tmp/notifications.json \
    && ok "Budget 'awslabs-monthly-cap' created at \$${limit}/mo -> alerts to ${email}" \
    || warn "Budget may already exist (that's fine)."
    ;;

  orphans)
    step "Lab resources still alive (tag project=awslabs):"
    aws resourcegroupstaggingapi get-resources \
      --tag-filters Key=project,Values=awslabs \
      --region "$AWS_REGION" \
      --query 'ResourceTagMappingList[].ResourceARN' --output table
    ;;

  nuke)
    warn "This destroys ALL resources tagged project=awslabs in ${AWS_REGION}."
    read -r -p "Type 'nuke' to confirm: " c
    [ "$c" = "nuke" ] || { echo "aborted"; exit 1; }
    for lab in "$HERE"/../labs/*/; do
      [ -f "$lab/terraform.tfstate" ] || continue
      warn "destroying via state: $(basename "$lab")"
      (cd "$lab" && terraform destroy -auto-approve) || warn "  destroy failed for $(basename "$lab")"
    done
    ok "Done. Re-check with: scripts/guardrails.sh orphans"
    ;;

  *)
    cat <<TXT
guardrails.sh — cost safety
  create-budget <USD>   Email budget alarm at 80%/100% of monthly cap (default \$5)
  orphans               List lab resources still alive (by tag)
  nuke                  Destroy all lab resources (confirm with 'nuke')
TXT
    ;;
esac
