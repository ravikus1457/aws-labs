#!/usr/bin/env bash
# Exercise + evidence capture for Lab 02 (EC2 + ALB + ASG).
# Receives the evidence dir as $1. Reads terraform outputs from $OUTPUTS_JSON.
set -euo pipefail
EVID="${1:?evidence dir required}"
REGION="${AWS_REGION:?}"

ALB_DNS="$(jq -r '.alb_dns_name.value' "$OUTPUTS_JSON")"
ASG_NAME="$(jq -r '.asg_name.value' "$OUTPUTS_JSON")"
TG_ARN="$(jq -r '.target_group_arn.value' "$OUTPUTS_JSON")"
URL="http://${ALB_DNS}/"

echo "Exercising web tier behind ALB: $ALB_DNS in $REGION"

# --- Record the AWS-side topology (handy evidence) --------------------------
echo "--- Auto Scaling Group ---"
aws autoscaling describe-auto-scaling-groups --region "$REGION" \
  --auto-scaling-group-names "$ASG_NAME" \
  --query 'AutoScalingGroups[0].{Name:AutoScalingGroupName,Min:MinSize,Max:MaxSize,Desired:DesiredCapacity,Instances:Instances[].{Id:InstanceId,AZ:AvailabilityZone,Health:HealthStatus,State:LifecycleState}}' \
  --output json | tee "$EVID/asg.json"

echo "--- Target group health ---"
aws elbv2 describe-target-health --region "$REGION" --target-group-arn "$TG_ARN" \
  --query 'TargetHealthDescriptions[].{Target:Target.Id,State:TargetHealth.State}' \
  --output table | tee "$EVID/target-health.txt" || true

# --- Poll the ALB until it serves an HTTP 200 -------------------------------
# ALB + new instances take ~1-2 min to register healthy, so retry.
echo "--- Polling $URL for HTTP 200 (up to ~120s) ---"
status=0
for i in $(seq 1 24); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$URL" || echo 000)"
  echo "attempt $i: HTTP $code"
  if [ "$code" = "200" ]; then status=200; break; fi
  sleep 5
done

# Capture headers + body once it's up (or the last attempt's output anyway).
curl -s -D "$EVID/response-headers.txt" -o "$EVID/response-body.txt" --max-time 5 "$URL" || true
echo "first HTTP code that returned 200: ${status}" | tee "$EVID/http-status.txt"

# --- Hit it several times, collect distinct instance-ids --------------------
echo "--- Sampling the ALB 8 times to observe load balancing ---"
: > "$EVID/responses.txt"
for i in $(seq 1 8); do
  body="$(curl -s --max-time 5 "$URL" || true)"
  echo "$body" >> "$EVID/responses.txt"
done
# index page looks like: "Hello from instance i-0123456789abcdef0"
grep -oE 'i-[0-9a-f]+' "$EVID/responses.txt" | sort -u > "$EVID/instance-ids.txt" || true
distinct="$(wc -l < "$EVID/instance-ids.txt" | tr -d ' ')"
echo "distinct instance ids observed: $distinct"
cat "$EVID/instance-ids.txt" || true

# --- Assertions -------------------------------------------------------------
echo "--- Assertions ---"
rc=0
if [ "$status" = "200" ]; then
  echo "PASS: ALB served HTTP 200"
else
  echo "FAIL: ALB never returned HTTP 200 after retries"
  rc=1
fi

if [ "$distinct" -ge 2 ]; then
  echo "PASS: load balanced across $distinct distinct instances"
elif [ "$distinct" -eq 1 ]; then
  echo "WARN: only 1 distinct instance id observed (sampling may have hit one target)"
else
  echo "WARN: no instance ids parsed from responses"
fi

exit "$rc"
