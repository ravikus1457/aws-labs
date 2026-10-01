#!/usr/bin/env bash
# Exercise + evidence capture for Lab 06 (ECS Fargate + CI/CD).
# Receives the evidence dir as $1. Reads terraform outputs from $OUTPUTS_JSON.
# Used by BOTH scripts/run-lab.sh (local) and the CI smoke job (same contract).
set -euo pipefail
EVID="${1:?evidence dir required}"
REGION="${AWS_REGION:?}"
OUTPUTS_JSON="${OUTPUTS_JSON:?path to terraform output -json}"
mkdir -p "$EVID"

ALB_URL="$(jq -r '.alb_url.value' "$OUTPUTS_JSON")"
TG_ARN="$(jq -r '.target_group_arn.value' "$OUTPUTS_JSON")"
CLUSTER="$(jq -r '.cluster_name.value' "$OUTPUTS_JSON")"
SERVICE="$(jq -r '.service_name.value' "$OUTPUTS_JSON")"
DESIRED="$(jq -r '.desired_count.value' "$OUTPUTS_JSON")"
REPO="$(jq -r '.ecr_repository_name.value' "$OUTPUTS_JSON")"
IMAGE_TAG="$(jq -r '.image_tag.value' "$OUTPUTS_JSON")"
IMAGE_URI="$(jq -r '.image_uri.value' "$OUTPUTS_JSON")"

echo "Exercising lab 06: $SERVICE on $CLUSTER behind $ALB_URL ($REGION)"
echo "Expected image: $IMAGE_URI"
rc=0

# ---------------------------------------------------------------------------
# 1. /healthz through the ALB — retries while targets register (~1-3 min).
# ---------------------------------------------------------------------------
echo "--- Polling ${ALB_URL}/healthz (up to ~3 min) ---"
HEALTH_CODE="000"
for i in $(seq 1 36); do
  HEALTH_CODE="$(curl -s -o "$EVID/healthz.json" -D "$EVID/healthz-headers.txt" -w '%{http_code}' --max-time 5 "${ALB_URL}/healthz" || echo 000)"
  echo "attempt $i/36: HTTP $HEALTH_CODE"
  [ "$HEALTH_CODE" = "200" ] && break
  sleep 5
done
echo "healthz HTTP code: $HEALTH_CODE" | tee "$EVID/healthz-code.txt"

# ---------------------------------------------------------------------------
# 2. /version — proves WHICH build is serving (APP_VERSION = image tag).
# ---------------------------------------------------------------------------
echo "--- GET /version ---"
curl -s --max-time 5 "${ALB_URL}/version" -o "$EVID/version.json" || true
cat "$EVID/version.json" 2>/dev/null || true; echo
SERVED_VERSION="$(jq -r '.version // empty' "$EVID/version.json" 2>/dev/null || true)"

# ---------------------------------------------------------------------------
# 3. Target health — expect every desired task healthy.
# ---------------------------------------------------------------------------
echo "--- Target group health ---"
aws elbv2 describe-target-health --region "$REGION" --target-group-arn "$TG_ARN" \
  --query 'TargetHealthDescriptions[].{Target:Target.Id,AZ:Target.AvailabilityZone,State:TargetHealth.State,Reason:TargetHealth.Reason}' \
  --output json | tee "$EVID/target-health.json"
HEALTHY="$(jq '[.[] | select(.State=="healthy")] | length' "$EVID/target-health.json")"
echo "healthy targets: $HEALTHY / desired $DESIRED"

# ---------------------------------------------------------------------------
# 4. Service summary + the digests of what is ACTUALLY running.
# ---------------------------------------------------------------------------
echo "--- ECS service ---"
aws ecs describe-services --region "$REGION" --cluster "$CLUSTER" --services "$SERVICE" \
  --query 'services[0].{Service:serviceName,Status:status,Desired:desiredCount,Running:runningCount,Pending:pendingCount,TaskDef:taskDefinition,Rollout:deployments[0].rolloutState}' \
  --output json | tee "$EVID/service.json"
RUNNING="$(jq -r '.Running' "$EVID/service.json")"

echo "--- Image digest in ECR vs. digest running in the tasks ---"
ECR_DIGEST="$(aws ecr describe-images --region "$REGION" --repository-name "$REPO" \
  --image-ids imageTag="$IMAGE_TAG" --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || echo "unknown")"
echo "ECR  $IMAGE_TAG -> $ECR_DIGEST" | tee "$EVID/image-digest.txt"

TASK_ARNS="$(aws ecs list-tasks --region "$REGION" --cluster "$CLUSTER" --service-name "$SERVICE" \
  --desired-status RUNNING --query 'taskArns' --output text 2>/dev/null || true)"
: > "$EVID/task-digests.txt"
if [ -n "$TASK_ARNS" ] && [ "$TASK_ARNS" != "None" ]; then
  # shellcheck disable=SC2086
  aws ecs describe-tasks --region "$REGION" --cluster "$CLUSTER" --tasks $TASK_ARNS \
    --query 'tasks[].{Task:taskArn,AZ:availabilityZone,Image:containers[0].image,Digest:containers[0].imageDigest,Health:healthStatus,Last:lastStatus}' \
    --output json | tee "$EVID/tasks.json"
  jq -r '.[] | "\(.Task | split("/")[-1])  \(.AZ)  \(.Digest)"' "$EVID/tasks.json" | tee "$EVID/task-digests.txt"
fi
TASK_DIGESTS="$(awk '{print $3}' "$EVID/task-digests.txt" | sort -u | grep -v '^null$' || true)"

# Scan findings for the deployed image (scan-on-push). Informational.
aws ecr describe-image-scan-findings --region "$REGION" --repository-name "$REPO" \
  --image-ids imageTag="$IMAGE_TAG" --query 'imageScanFindings.findingSeverityCounts' \
  --output json > "$EVID/scan-findings.json" 2>/dev/null || echo '{}' > "$EVID/scan-findings.json"
echo "scan severity counts: $(cat "$EVID/scan-findings.json")"

# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------
echo "--- Assertions ---"
if [ "$HEALTH_CODE" = "200" ] && [ "$(jq -r '.status' "$EVID/healthz.json" 2>/dev/null)" = "ok" ]; then
  echo "PASS: ALB /healthz returned 200 {\"status\":\"ok\"}"
else
  echo "FAIL: ALB /healthz never returned 200 (last: $HEALTH_CODE)"
  rc=1
fi

if [ "$HEALTHY" -ge "$DESIRED" ] 2>/dev/null; then
  echo "PASS: $HEALTHY healthy targets (desired $DESIRED)"
else
  echo "FAIL: only $HEALTHY healthy targets (desired $DESIRED)"
  rc=1
fi

if [ "$RUNNING" = "$DESIRED" ]; then
  echo "PASS: service runningCount == desiredCount ($RUNNING)"
else
  echo "FAIL: service runningCount $RUNNING != desiredCount $DESIRED"
  rc=1
fi

if [ "$ECR_DIGEST" != "unknown" ] && [ -n "$TASK_DIGESTS" ]; then
  if [ "$(printf '%s\n' "$TASK_DIGESTS" | wc -l | tr -d ' ')" = "1" ] && [ "$TASK_DIGESTS" = "$ECR_DIGEST" ]; then
    echo "PASS: every running task runs the ECR digest for tag $IMAGE_TAG ($ECR_DIGEST)"
  else
    echo "FAIL: running task digest(s) differ from ECR tag $IMAGE_TAG"
    printf '  ecr:  %s\n  task: %s\n' "$ECR_DIGEST" "$TASK_DIGESTS"
    rc=1
  fi
else
  echo "WARN: could not compare digests (ecr=$ECR_DIGEST, tasks=${TASK_DIGESTS:-none})"
fi

if [ -n "$SERVED_VERSION" ] && [ "$SERVED_VERSION" = "$IMAGE_TAG" ]; then
  echo "PASS: /version reports the deployed tag ($SERVED_VERSION)"
else
  echo "WARN: /version reported '${SERVED_VERSION:-<none>}' (expected image tag $IMAGE_TAG)"
fi

exit "$rc"
