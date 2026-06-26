#!/usr/bin/env bash
# Exercise + evidence capture for Lab 05 (CloudWatch monitoring).
# Receives the evidence dir as $1. Reads terraform outputs from $OUTPUTS_JSON.
#
# Goal: prove the alarm reacts to a threshold breach. We push a breaching
# datapoint (real-world path), then force the state transition deterministically
# so the assertion doesn't depend on CloudWatch's multi-minute evaluation cycle.
set -euo pipefail
EVID="${1:?evidence dir required}"
REGION="${AWS_REGION:?}"

ALARM_NAME="$(jq -r '.alarm_name.value' "$OUTPUTS_JSON")"
SNS_TOPIC_ARN="$(jq -r '.sns_topic_arn.value' "$OUTPUTS_JSON")"
DASHBOARD_NAME="$(jq -r '.dashboard_name.value' "$OUTPUTS_JSON")"
SERVICE="$(jq -r '.service_dimension.value' "$OUTPUTS_JSON")"

echo "Exercising CloudWatch alarm '$ALARM_NAME' (topic $SNS_TOPIC_ARN) in $REGION"

echo "--- SNS topic ---"
aws sns get-topic-attributes --topic-arn "$SNS_TOPIC_ARN" --region "$REGION" \
  --query 'Attributes.{TopicArn:TopicArn,Owner:Owner}' --output table \
  | tee "$EVID/sns-topic.txt"

echo "--- Initial alarm state (expect OK or INSUFFICIENT_DATA) ---"
aws cloudwatch describe-alarms --alarm-names "$ALARM_NAME" --region "$REGION" \
  --query 'MetricAlarms[0].{Name:AlarmName,State:StateValue,Threshold:Threshold,Metric:MetricName}' \
  --output json | tee "$EVID/alarm-before.json"

echo "--- Push a breaching datapoint to the custom metric (real-world path) ---"
aws cloudwatch put-metric-data --namespace "AwsLabs/Demo" --metric-name "DemoLoad" \
  --dimensions "Service=$SERVICE" --value 95 --region "$REGION"
echo "pushed DemoLoad=95 for Service=$SERVICE"

# A real metric alarm can take several minutes to re-evaluate, which is too slow
# (and flaky) for a deterministic lab. set-alarm-state forces the transition so
# we can assert on it immediately — this is also how you test alarm actions.
echo "--- Force alarm into ALARM state (deterministic) ---"
aws cloudwatch set-alarm-state --alarm-name "$ALARM_NAME" --region "$REGION" \
  --state-value ALARM --state-reason "lab exercise: simulated high load"

# Give CloudWatch a moment to record the transition.
sleep 5

echo "--- Alarm state after breach ---"
aws cloudwatch describe-alarms --alarm-names "$ALARM_NAME" --region "$REGION" \
  --query 'MetricAlarms[0].{Name:AlarmName,State:StateValue,Reason:StateReason}' \
  --output json | tee "$EVID/alarm-after.json"

echo "--- Dashboard ---"
aws cloudwatch get-dashboard --dashboard-name "$DASHBOARD_NAME" --region "$REGION" \
  --query 'DashboardName' --output text | tee "$EVID/dashboard.txt"

# --- Assertions ------------------------------------------------------------
echo "--- Assertions ---"
rc=0
state_after="$(jq -r '.State' "$EVID/alarm-after.json")"
if [ "$state_after" = "ALARM" ]; then
  echo "PASS: alarm fired on threshold breach (StateValue=ALARM)"
else
  echo "FAIL: alarm did not reach ALARM (StateValue=$state_after)"
  rc=1
fi

# Reset the alarm state so a re-run starts clean (cleanup only; not asserted).
echo "--- Reset alarm state to OK ---"
aws cloudwatch set-alarm-state --alarm-name "$ALARM_NAME" --region "$REGION" \
  --state-value OK --state-reason "lab exercise: reset after test" || true

exit "$rc"
