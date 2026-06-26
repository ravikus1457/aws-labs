#!/usr/bin/env bash
#
# run-lab.sh — the automated lab runner ("the agent").
#
# For a single lab it does the full loop:
#   preflight -> terraform init/validate/apply -> exercise (capture evidence)
#   -> terraform destroy (ALWAYS, even on failure, unless --keep)
#   -> write a markdown evidence report.
#
# Usage:
#   scripts/run-lab.sh labs/01-vpc-networking
#   scripts/run-lab.sh labs/02-ec2-alb-asg --keep        # leave it running (you pay!)
#   scripts/run-lab.sh labs/01-vpc-networking --plan-only # no apply, just show plan
#
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=lib.sh
source "$HERE/lib.sh"

KEEP=0; PLAN_ONLY=0; DESTROY=1
LAB=""
for arg in "$@"; do
  case "$arg" in
    --keep)      KEEP=1; DESTROY=0 ;;
    --no-destroy) DESTROY=0 ;;
    --plan-only) PLAN_ONLY=1; DESTROY=0 ;;
    -*)          err "unknown flag: $arg"; exit 2 ;;
    *)           LAB="$arg" ;;
  esac
done

[ -n "$LAB" ] || { err "usage: run-lab.sh <lab-dir> [--keep|--plan-only]"; exit 2; }
LAB_PATH="$(cd "$ROOT/$LAB" 2>/dev/null && pwd || cd "$LAB" 2>/dev/null && pwd || true)"
[ -n "$LAB_PATH" ] && [ -d "$LAB_PATH" ] || { err "lab not found: $LAB"; exit 2; }
LAB_NAME="$(basename "$LAB_PATH")"

preflight

RUN_ID="$(new_run_id)"
EVID_DIR="$ROOT/evidence/${LAB_NAME}-${RUN_ID}"
mkdir -p "$EVID_DIR"
export LAB_NAME RUN_ID EVID_DIR AWS_REGION ACCOUNT_ID

# Common Terraform variables every lab understands (for tagging + naming).
export TF_VAR_run_id="$RUN_ID"
export TF_VAR_project="awslabs"
export TF_VAR_aws_region="$AWS_REGION"

cd "$LAB_PATH"

# --- teardown trap: this is the cost-safety guarantee -------------------------
cleanup() {
  local code=$?
  if [ "$DESTROY" -eq 1 ]; then
    step "Tearing down (terraform destroy) — keeps your bill near \$0"
    if terraform destroy -auto-approve -input=false >>"$EVID_DIR/terraform-destroy.log" 2>&1; then
      ok "destroyed cleanly"
    else
      err "DESTROY FAILED — resources for run ${RUN_ID} may still exist and COST money."
      err "Inspect: aws resourcegroupstaggingapi get-resources --tag-filters Key=run_id,Values=${RUN_ID} --region ${AWS_REGION}"
      err "Re-run teardown: (cd '$LAB_PATH' && terraform destroy -auto-approve)"
      code=1
    fi
  elif [ "$KEEP" -eq 1 ]; then
    warn "--keep set: resources LEFT RUNNING. You are being billed."
    warn "Destroy when done:  (cd '$LAB_PATH' && terraform destroy -auto-approve)"
  fi
  exit "$code"
}
trap cleanup EXIT

step "Lab: ${LAB_NAME}   run: ${RUN_ID}"
log  "evidence -> ${EVID_DIR}"

step "terraform init"
terraform init -input=false >>"$EVID_DIR/terraform-init.log" 2>&1
ok "initialised"

step "terraform validate"
terraform validate | tee "$EVID_DIR/terraform-validate.txt"

if [ "$PLAN_ONLY" -eq 1 ]; then
  step "terraform plan (plan-only mode, no resources created)"
  terraform plan -input=false | tee "$EVID_DIR/terraform-plan.txt"
  ok "plan-only complete. Nothing was created."
  exit 0
fi

step "terraform apply"
terraform apply -auto-approve -input=false 2>&1 | tee "$EVID_DIR/terraform-apply.log"
ok "infrastructure is live"

# Capture outputs as JSON for the exercise + the report.
terraform output -json > "$EVID_DIR/outputs.json" 2>/dev/null || echo '{}' > "$EVID_DIR/outputs.json"

# --- exercise the scenario + capture evidence ---------------------------------
if [ -x "./exercise.sh" ] || [ -f "./exercise.sh" ]; then
  step "Exercising scenario (capturing evidence)"
  # exercise.sh receives the evidence dir as $1 and outputs.json on stdin-path via env.
  EVID_DIR="$EVID_DIR" OUTPUTS_JSON="$EVID_DIR/outputs.json" \
    bash ./exercise.sh "$EVID_DIR" 2>&1 | tee "$EVID_DIR/exercise.log" || warn "exercise reported a non-zero exit"
  ok "evidence captured"
else
  warn "no exercise.sh in this lab — skipping scenario step"
fi

# --- write the evidence report ------------------------------------------------
step "Writing evidence report"
{
  echo "# Evidence report — ${LAB_NAME}"
  echo
  echo "- **Run ID:** \`${RUN_ID}\`"
  echo "- **Account:** \`${ACCOUNT_ID}\`"
  echo "- **Region:** \`${AWS_REGION}\`"
  echo "- **Date (UTC):** $(date -u '+%Y-%m-%d %H:%M:%S')"
  echo
  echo "## Terraform outputs"
  echo '```json'
  cat "$EVID_DIR/outputs.json"
  echo '```'
  echo
  if [ -f "$EVID_DIR/exercise.log" ]; then
    echo "## Scenario exercise log"
    echo '```'
    cat "$EVID_DIR/exercise.log"
    echo '```'
  fi
} > "$EVID_DIR/REPORT.md"

ok "report -> ${EVID_DIR}/REPORT.md"
# trap will now destroy unless --keep
