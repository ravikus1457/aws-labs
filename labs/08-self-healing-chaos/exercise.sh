#!/usr/bin/env bash
# Smoke test + evidence for Lab 08 (self-healing chaos). Runs BEFORE any chaos:
# it proves every piece the experiments depend on is actually wired, so a red
# chaos run means "the system did not heal", not "the lab was never set up".
#
#   exercise.sh <evidence_dir>        reads terraform outputs from $OUTPUTS_JSON
#
# Same contract as labs 06/07: used by scripts/run-lab.sh locally and by the CI
# smoke job. Needs: aws, jq, curl.  Env knobs:
#   EXERCISE_WIRING=1   force the cpu-high alarm into ALARM with set-alarm-state and
#                       watch EventBridge -> runbook scale OUT, then back IN (default 1).
#                       Costs one extra t3.micro for a few minutes; no FIS involved.
set -euo pipefail
EVID="${1:?evidence dir required}"
OUTPUTS_JSON="${OUTPUTS_JSON:?path to terraform output -json}"
mkdir -p "$EVID"

o() { jq -r "$1" "$OUTPUTS_JSON"; }
REGION="${AWS_REGION:-$(o '.aws_region.value')}"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
URL="$(o '.alb_url.value')"
TG="$(o '.target_group_arn.value')"
ASG="$(o '.asg_name.value')"
BASELINE="$(o '.baseline_capacity.value')"
CANARY="$(o '.canary_name.value')"
RUNBOOK="$(o '.runbook_name.value')"
DLQ="$(o '.remediation_dlq_url.value // empty')"
IFACE="$(o '.fault_parameters.value.network_interface')"
ALARM_CPU="$(o '.alarm_names.value.cpu_high')"
ALARM_CANARY="$(o '.alarm_names.value.canary')"
EXERCISE_WIRING="${EXERCISE_WIRING:-1}"
rc=0
RESULTS=()
pass() { RESULTS+=("PASS: $*"); }
fail() { RESULTS+=("FAIL: $*"); rc=1; }
warn() { RESULTS+=("WARN: $*"); }

healthy_count() {
  aws elbv2 describe-target-health --target-group-arn "$TG" \
    --query "length(TargetHealthDescriptions[?TargetHealth.State=='healthy'])" --output text 2>/dev/null || echo 0
}
asg_desired() { aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG" --query 'AutoScalingGroups[0].DesiredCapacity' --output text; }

echo "Exercising lab 08: $URL (ASG $ASG, baseline $BASELINE, region $REGION)"

# ---------------------------------------------------------------------------
# 1. The app answers through the ALB (DNS + target registration can take minutes).
# ---------------------------------------------------------------------------
echo "--- GET /healthz through the ALB ---"
CODE=000
for i in $(seq 1 30); do
  CODE="$(curl -s -o "$EVID/healthz.json" -w '%{http_code}' --max-time 5 "$URL/healthz" || true)"
  [ "$CODE" = "200" ] && break
  echo "attempt $i/30: HTTP $CODE"; sleep 10
done
if [ "$CODE" = "200" ] && [ "$(jq -r '.status' "$EVID/healthz.json" 2>/dev/null)" = "ok" ]; then
  pass "ALB /healthz returned 200 {\"status\":\"ok\"}"
else
  fail "ALB /healthz never returned 200 (last: $CODE)"
fi

# 2. Load balancing really spreads across instances (and AZs).
: > "$EVID/root-responses.jsonl"
for i in $(seq 1 30); do curl -s --max-time 5 "$URL/" >> "$EVID/root-responses.jsonl" && echo >> "$EVID/root-responses.jsonl" || true; done
DISTINCT="$(jq -r 'select(.instance) | .instance' "$EVID/root-responses.jsonl" 2>/dev/null | sort -u | wc -l | tr -d ' ')"
AZS="$(jq -r 'select(.az) | .az' "$EVID/root-responses.jsonl" 2>/dev/null | sort -u | tr '\n' ' ')"
if [ "$DISTINCT" -ge "$BASELINE" ]; then pass "30 requests reached $DISTINCT distinct instances (AZs: $AZS)"; else fail "30 requests reached only $DISTINCT instance(s); expected $BASELINE"; fi

# 3. Target group + ASG at baseline.
HEALTHY="$(healthy_count)"
aws elbv2 describe-target-health --target-group-arn "$TG" --output json > "$EVID/target-health.json"
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG" --output json > "$EVID/asg.json"
if [ "$HEALTHY" = "$BASELINE" ]; then pass "$HEALTHY/$BASELINE targets healthy"; else fail "$HEALTHY targets healthy, expected $BASELINE"; fi
HC_TYPE="$(jq -r '.AutoScalingGroups[0].HealthCheckType' "$EVID/asg.json")"
if [ "$HC_TYPE" = "ELB" ]; then pass "ASG health check type is ELB (unhealthy-in-the-ALB instances get replaced, not just stopped ones)"; else fail "ASG health check type is $HC_TYPE, expected ELB"; fi

# ---------------------------------------------------------------------------
# 4. FIS reaches instances through SSM. If the agent is offline or stress-ng / tc
#    are missing, the cpu and latency experiments would fail AFTER being billed.
# ---------------------------------------------------------------------------
echo "--- SSM preflight (what the FIS documents need) ---"
IDS="$(jq -r '.AutoScalingGroups[0].Instances[] | select(.LifecycleState=="InService") | .InstanceId' "$EVID/asg.json" | tr '\n' ' ')"
ONLINE=0
for i in $(seq 1 30); do
  # shellcheck disable=SC2086
  ONLINE="$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$(echo $IDS | tr ' ' ',')" \
    --query "length(InstanceInformationList[?PingStatus=='Online'])" --output text 2>/dev/null || echo 0)"
  [ "$ONLINE" -ge "$BASELINE" ] && break
  echo "attempt $i/30: $ONLINE/$BASELINE instances Online in SSM"; sleep 10
done
if [ "$ONLINE" -ge "$BASELINE" ]; then pass "$ONLINE instances registered Online with SSM (FIS send-command can reach them)"; else fail "only $ONLINE/$BASELINE instances Online in SSM"; fi

TOOLS_OK=0
if [ "$ONLINE" -ge 1 ]; then
  # shellcheck disable=SC2086
  CMD_ID="$(aws ssm send-command --document-name AWS-RunShellScript --instance-ids $IDS \
    --comment "lab08 preflight: FIS dependencies" \
    --parameters "{\"commands\":[\"command -v stress-ng && command -v tc && ip -o link show ${IFACE} | cut -d: -f2 && systemctl is-active lab08-app\"]}" \
    --query 'Command.CommandId' --output text)"
  for i in $(seq 1 18); do
    sleep 5
    PENDING="$(aws ssm list-command-invocations --command-id "$CMD_ID" --query "length(CommandInvocations[?Status=='Pending' || Status=='InProgress' || Status=='Delayed'])" --output text)"
    [ "$PENDING" = "0" ] && break
  done
  aws ssm list-command-invocations --command-id "$CMD_ID" --details --output json > "$EVID/ssm-preflight.json"
  TOOLS_OK="$(jq '[.CommandInvocations[] | select(.Status=="Success")] | length' "$EVID/ssm-preflight.json")"
fi
if [ "$TOOLS_OK" -ge "$BASELINE" ]; then
  pass "stress-ng, tc, interface $IFACE and lab08-app present on $TOOLS_OK instances"
else
  fail "FIS dependency check succeeded on $TOOLS_OK/$BASELINE instances (see ssm-preflight.json: stress-ng / tc / $IFACE / app)"
fi

# ---------------------------------------------------------------------------
# 5. FIS templates exist and every one carries the canary stop condition.
# ---------------------------------------------------------------------------
CANARY_ALARM_ARN="$(aws cloudwatch describe-alarms --alarm-names "$ALARM_CANARY" --query 'MetricAlarms[0].AlarmArn' --output text)"
for name in terminate cpu_stress network_latency; do
  TID="$(o ".fis_template_ids.value.${name}")"
  aws fis get-experiment-template --id "$TID" --output json > "$EVID/fis-template-${name}.json" 2>/dev/null || echo '{}' > "$EVID/fis-template-${name}.json"
  STOP="$(jq -r '.experimentTemplate.stopConditions[0].value // empty' "$EVID/fis-template-${name}.json")"
  if [ "$STOP" = "$CANARY_ALARM_ARN" ]; then pass "FIS template $name ($TID) exists, stop condition = canary alarm"; else fail "FIS template $name: stop condition '$STOP' != canary alarm"; fi
done

# 6. All four alarms exist.
mapfile -t ALARM_LIST < <(o '.alarm_names.value[]')
aws cloudwatch describe-alarms --alarm-names "${ALARM_LIST[@]}" --output json > "$EVID/alarms.json"
NALARMS="$(jq '.MetricAlarms | length' "$EVID/alarms.json")"
if [ "$NALARMS" = "4" ]; then pass "4 alarms present: $(jq -r '[.MetricAlarms[] | .AlarmName + "=" + .StateValue] | join(", ")' "$EVID/alarms.json")"; else fail "$NALARMS/4 alarms present"; fi

# ---------------------------------------------------------------------------
# 7. Canary is running and has passed at least once (first run takes a few minutes).
# ---------------------------------------------------------------------------
echo "--- Synthetics canary ---"
PASSED=0
for i in $(seq 1 30); do
  aws synthetics get-canary-runs --name "$CANARY" --max-results 20 --output json > "$EVID/canary-runs.json" 2>/dev/null || echo '{"CanaryRuns":[]}' > "$EVID/canary-runs.json"
  PASSED="$(jq '[.CanaryRuns[] | select(.Status.State=="PASSED")] | length' "$EVID/canary-runs.json")"
  [ "$PASSED" -ge 1 ] && break
  echo "attempt $i/30: canary has no PASSED run yet"; sleep 15
done
aws synthetics get-canary --name "$CANARY" --output json > "$EVID/canary.json"
CSTATE="$(jq -r '.Canary.Status.State' "$EVID/canary.json")"
if [ "$CSTATE" = "RUNNING" ] && [ "$PASSED" -ge 1 ]; then pass "canary $CANARY RUNNING on $(jq -r '.Canary.RuntimeVersion' "$EVID/canary.json"), $PASSED passed run(s)"; else fail "canary state $CSTATE, $PASSED passed runs"; fi

# ---------------------------------------------------------------------------
# 8. Remediation wiring, without FIS: push the cpu-high alarm into ALARM by hand.
#    CloudWatch emits the state change, EventBridge must start the runbook, the
#    runbook must add an instance; at the next evaluation the alarm returns to its
#    real state (OK) and the second rule must scale back in. The detector-to-fix
#    path is proven to FIRE before any experiment depends on it.
# ---------------------------------------------------------------------------
if [ "$EXERCISE_WIRING" = "1" ]; then
  echo "--- remediation wiring: set-alarm-state ALARM on $ALARM_CPU ---"
  T_WIRE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  aws cloudwatch set-alarm-state --alarm-name "$ALARM_CPU" --state-value ALARM \
    --state-reason "lab08 smoke test: prove alarm -> EventBridge -> runbook fires"
  OUT_OK=0; IN_OK=0
  for i in $(seq 1 36); do
    sleep 10
    aws ssm describe-automation-executions \
      --filters "Key=DocumentNamePrefix,Values=$RUNBOOK" "Key=StartTimeAfter,Values=$T_WIRE" \
      --output json > "$EVID/wiring-automations.json" 2>/dev/null || true
    DES="$(asg_desired)"
    if [ "$OUT_OK" = "0" ] && [ "$DES" -gt "$BASELINE" ]; then OUT_OK=1; echo "scale-out observed: desired=$DES after $((i * 10)) s"; fi
    if [ "$OUT_OK" = "1" ] && [ "$DES" = "$BASELINE" ]; then IN_OK=1; echo "scale-in observed: desired=$DES after $((i * 10)) s"; break; fi
  done
  NEXEC="$(jq '.AutomationExecutionMetadataList | length' "$EVID/wiring-automations.json" 2>/dev/null || echo 0)"
  NSUCC="$(jq '[.AutomationExecutionMetadataList[] | select(.AutomationExecutionStatus=="Success")] | length' "$EVID/wiring-automations.json" 2>/dev/null || echo 0)"
  if [ "$OUT_OK" = "1" ]; then pass "forced ALARM -> EventBridge -> $RUNBOOK scaled the ASG out ($NSUCC/$NEXEC executions Success)"; else
    fail "forced ALARM did not scale the ASG out within 6 min ($NEXEC runbook executions)"
    if [ -n "$DLQ" ]; then   # EventBridge parks a failed StartAutomationExecution here, with the reason
      aws sqs receive-message --queue-url "$DLQ" --max-number-of-messages 5 --wait-time-seconds 5 \
        --message-attribute-names All --output json > "$EVID/remediation-dlq.json" || true
      jq -r '.Messages[]?.MessageAttributes | "EventBridge target error: \(.ERROR_CODE.StringValue // "?"): \(.ERROR_MESSAGE.StringValue // "?")"' \
        "$EVID/remediation-dlq.json" 2>/dev/null || echo "remediation DLQ unreadable"
    fi
    # What IAM actually evaluated: the events role's DEPLOYED policy (not the source).
    aws iam get-role-policy --role-name "$(o '.name_prefix.value // "awslabs-lab08"')-events" \
      --policy-name "$(o '.name_prefix.value // "awslabs-lab08"')-events" --query PolicyDocument --output json \
      > "$EVID/events-role-policy.json" 2>&1 || true
    echo "deployed events role policy:"; jq -c '.Statement[]? | {Sid, Resource}' "$EVID/events-role-policy.json" 2>/dev/null || cat "$EVID/events-role-policy.json"
  fi
  if [ "$IN_OK" = "1" ]; then pass "alarm back to OK -> runbook scaled in to $BASELINE"; else warn "scale-in back to $BASELINE not observed within 6 min (desired=$(asg_desired)); chaos.sh waits for steady state anyway"; fi
else
  warn "remediation wiring test skipped (EXERCISE_WIRING=0)"
fi

echo "--- Assertions ---"
printf '%s\n' "${RESULTS[@]}"
echo "app: $URL/   canary: $CANARY   runbook: $RUNBOOK"
exit "$rc"
