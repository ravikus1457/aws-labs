#!/usr/bin/env bash
# Exercise + evidence capture for Lab 03 (IAM least-privilege vs S3).
# Receives the evidence dir as $1. Reads terraform outputs from $OUTPUTS_JSON.
#
# Strategy: instead of assuming the role and trying real S3 calls (which would
# need extra permissions and risk cost), we ask the IAM Policy Simulator what
# the role IS and IS NOT allowed to do. The runner identity only needs
# iam:SimulatePrincipalPolicy.
set -euo pipefail
EVID="${1:?evidence dir required}"
REGION="${AWS_REGION:?}"

ROLE_ARN="$(jq -r '.role_arn.value' "$OUTPUTS_JSON")"
BUCKET_ARN="$(jq -r '.bucket_arn.value' "$OUTPUTS_JSON")"
BUCKET_NAME="$(jq -r '.bucket_name.value' "$OUTPUTS_JSON")"

echo "Verifying least-privilege for role $ROLE_ARN against bucket $BUCKET_NAME"

rc=0

# ---------------------------------------------------------------------------
# 1) Actions that SHOULD be allowed: GetObject + PutObject on an object.
# ---------------------------------------------------------------------------
echo "--- Simulating ALLOWED actions (s3:GetObject, s3:PutObject) ---"
aws iam simulate-principal-policy \
  --policy-source-arn "$ROLE_ARN" \
  --action-names s3:GetObject s3:PutObject \
  --resource-arns "${BUCKET_ARN}/test.txt" \
  --region "$REGION" \
  --output json | tee "$EVID/sim-allowed.json"

allowed_count="$(jq -r '[.EvaluationResults[] | select(.EvalDecision == "allowed")] | length' "$EVID/sim-allowed.json")"
total_allowed="$(jq -r '.EvaluationResults | length' "$EVID/sim-allowed.json")"
if [ "$allowed_count" -eq "$total_allowed" ] && [ "$total_allowed" -eq 2 ]; then
  echo "PASS: role is allowed to GetObject and PutObject in its bucket"
else
  echo "FAIL: expected 2 allowed decisions, got $allowed_count/$total_allowed"
  rc=1
fi

# ---------------------------------------------------------------------------
# 2) Actions that SHOULD be denied: DeleteBucket + ec2:RunInstances.
#    A least-privilege policy grants neither, so both are implicitDeny.
# ---------------------------------------------------------------------------
echo "--- Simulating DENIED actions (s3:DeleteBucket, ec2:RunInstances) ---"
aws iam simulate-principal-policy \
  --policy-source-arn "$ROLE_ARN" \
  --action-names s3:DeleteBucket ec2:RunInstances \
  --resource-arns "*" \
  --region "$REGION" \
  --output json | tee "$EVID/sim-denied.json"

denied_count="$(jq -r '[.EvaluationResults[] | select(.EvalDecision == "implicitDeny")] | length' "$EVID/sim-denied.json")"
total_denied="$(jq -r '.EvaluationResults | length' "$EVID/sim-denied.json")"
if [ "$denied_count" -eq "$total_denied" ] && [ "$total_denied" -eq 2 ]; then
  echo "PASS: least privilege enforced — DeleteBucket and RunInstances are implicitly denied"
else
  echo "FAIL: expected 2 implicitDeny decisions, got $denied_count/$total_denied"
  rc=1
fi

# ---------------------------------------------------------------------------
# 3) Bucket must be fully private (all four public-access blocks true).
# ---------------------------------------------------------------------------
echo "--- Public access block for $BUCKET_NAME ---"
aws s3api get-public-access-block --bucket "$BUCKET_NAME" --region "$REGION" \
  --output json | tee "$EVID/public-access-block.json"

pab_all_true="$(jq -r '
  .PublicAccessBlockConfiguration as $c |
  ($c.BlockPublicAcls and $c.BlockPublicPolicy and $c.IgnorePublicAcls and $c.RestrictPublicBuckets)
' "$EVID/public-access-block.json")"
if [ "$pab_all_true" = "true" ]; then
  echo "PASS: bucket is private — all four public-access-block settings are true"
else
  echo "FAIL: bucket public-access-block is not fully enabled"
  rc=1
fi

echo "--- Assertions complete (rc=$rc) ---"
exit "$rc"
