#!/usr/bin/env bash
# Exercise + evidence capture for Lab 04 (ECS Fargate).
# Receives the evidence dir as $1. Reads terraform outputs from $OUTPUTS_JSON.
set -euo pipefail
EVID="${1:?evidence dir required}"
REGION="${AWS_REGION:?}"

CLUSTER="$(jq -r '.cluster_name.value' "$OUTPUTS_JSON")"
SERVICE="$(jq -r '.service_name.value' "$OUTPUTS_JSON")"

echo "Verifying ECS Fargate service $SERVICE on cluster $CLUSTER in $REGION"

rc=0

# ---------------------------------------------------------------------------
# 1. Wait for the service to schedule a RUNNING task.
# ---------------------------------------------------------------------------
echo "--- Waiting for a RUNNING task ---"
TASK_ARN=""
for i in $(seq 1 30); do
  TASK_ARN="$(aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" \
    --desired-status RUNNING --region "$REGION" \
    --query 'taskArns[0]' --output text 2>/dev/null || true)"
  if [ -n "$TASK_ARN" ] && [ "$TASK_ARN" != "None" ]; then
    echo "RUNNING task: $TASK_ARN"
    break
  fi
  echo "  attempt $i/30: no running task yet, retrying in 5s..."
  sleep 5
done

if [ -z "$TASK_ARN" ] || [ "$TASK_ARN" = "None" ]; then
  echo "FAIL: no RUNNING task ever appeared" | tee "$EVID/task.txt"
  exit 1
fi
echo "$TASK_ARN" > "$EVID/task-arn.txt"

# ---------------------------------------------------------------------------
# 2. Resolve the task's ENI -> public IP.
# ---------------------------------------------------------------------------
echo "--- Resolving public IP ---"
aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" --region "$REGION" \
  --output json > "$EVID/describe-tasks.json"

ENI_ID="$(jq -r '.tasks[0].attachments[].details[]? | select(.name=="networkInterfaceId") | .value' "$EVID/describe-tasks.json" | head -n1)"
echo "ENI: $ENI_ID"

PUBLIC_IP="$(aws ec2 describe-network-interfaces --network-interface-ids "$ENI_ID" --region "$REGION" \
  --query 'NetworkInterfaces[0].Association.PublicIp' --output text)"
echo "Public IP: $PUBLIC_IP" | tee "$EVID/public-ip.txt"

if [ -z "$PUBLIC_IP" ] || [ "$PUBLIC_IP" = "None" ]; then
  echo "FAIL: task has no public IP"
  exit 1
fi

# ---------------------------------------------------------------------------
# 3. Poll the container over HTTP until it serves the nginx welcome page.
# ---------------------------------------------------------------------------
echo "--- Polling http://$PUBLIC_IP/ ---"
HTTP_CODE=""
for i in $(seq 1 24); do
  HTTP_CODE="$(curl -s -o "$EVID/response.html" -w '%{http_code}' --max-time 5 "http://$PUBLIC_IP/" || echo "000")"
  if [ "$HTTP_CODE" = "200" ]; then
    echo "HTTP 200 from container"
    break
  fi
  echo "  attempt $i/24: got HTTP $HTTP_CODE, retrying in 5s..."
  sleep 5
done
echo "final HTTP code: $HTTP_CODE" | tee "$EVID/http-code.txt"

# ---------------------------------------------------------------------------
# 4. Service summary (running vs desired).
# ---------------------------------------------------------------------------
echo "--- Service summary ---"
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --region "$REGION" \
  --query 'services[0].{Service:serviceName,Status:status,Desired:desiredCount,Running:runningCount}' \
  --output json | tee "$EVID/service.json"
RUNNING_COUNT="$(jq -r '.Running' "$EVID/service.json")"

# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------
echo "--- Assertions ---"
if [ "$HTTP_CODE" = "200" ]; then
  echo "PASS: container reachable over HTTP (200)"
else
  echo "FAIL: container never returned HTTP 200 (last: $HTTP_CODE)"
  rc=1
fi

if [ -n "$RUNNING_COUNT" ] && [ "$RUNNING_COUNT" -ge 1 ] 2>/dev/null; then
  echo "PASS: service runningCount >= 1 ($RUNNING_COUNT)"
else
  echo "FAIL: service runningCount < 1 ($RUNNING_COUNT)"
  rc=1
fi

exit "$rc"
