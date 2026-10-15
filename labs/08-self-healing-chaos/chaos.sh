#!/usr/bin/env bash
# Run the lab 08 chaos experiments one at a time and MEASURE what happened.
#
#   chaos.sh <evidence_dir> [experiment ...]
#     experiments: terminate cpu_stress network_latency
#     default    : terminate cpu_stress  (+ network_latency when CHAOS_LATENCY=1)
#
# Per experiment:
#   1. wait for steady state (baseline healthy, ASG at baseline, no alarm in ALARM,
#      no FIS experiment running) - never stack a fault on an unhealed system
#   2. start two background recorders against the LIVE system:
#        probe   : GET /healthz through the ALB every 2 s  -> raw/<exp>/probes.tsv
#        sampler : target health + ASG desired/InService every 5 s -> raw/<exp>/samples.jsonl
#   3. fis start-experiment, wait for it to finish, then wait for RECOVERY (not a timer):
#        terminate     : healthy targets dipped and came back to baseline, and the
#                        healthy-hosts alarm has had its say (ALARM->OK, or 5 min of silence)
#        cpu_stress    : cpu-high went ALARM then OK, and the ASG is back at baseline
#        network_latency: latency-high went ALARM then OK
#   4. collect the records AWS kept (FIS experiment, alarm history, runbook executions,
#      canary runs, ASG activities) and hand everything to measure.py
#
# Writes <evidence_dir>/<experiment>.json, <evidence_dir>/chaos-report.md and the raw
# records under <evidence_dir>/raw/<experiment>/. Exit 1 if any experiment FAILed.
#
# COST: FIS bills $0.10 per action-minute: terminate ~1 min, cpu_stress ~4 min,
# network_latency ~3 min. Needs: aws, jq, curl, python3.
set -euo pipefail
EVID="${1:?evidence dir required}"; shift || true
OUTPUTS_JSON="${OUTPUTS_JSON:?path to terraform output -json}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$EVID/raw"

o() { jq -r "$1" "$OUTPUTS_JSON"; }
REGION="${AWS_REGION:-$(o '.aws_region.value')}"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
URL="$(o '.alb_url.value')"
TG="$(o '.target_group_arn.value')"
ASG="$(o '.asg_name.value')"
BASELINE="$(o '.baseline_capacity.value')"
CANARY="$(o '.canary_name.value')"
RUNBOOK="$(o '.runbook_name.value')"
ALARM_CANARY="$(o '.alarm_names.value.canary')"
ALARM_HEALTHY="$(o '.alarm_names.value.healthy_hosts')"
ALARM_CPU="$(o '.alarm_names.value.cpu_high')"
ALARM_LAT="$(o '.alarm_names.value.latency_high')"
PROBE_INTERVAL="${PROBE_INTERVAL:-2}"
SAMPLE_INTERVAL="${SAMPLE_INTERVAL:-5}"
STEADY_TIMEOUT="${STEADY_TIMEOUT:-900}"
EXPERIMENT_TIMEOUT="${EXPERIMENT_TIMEOUT:-900}"
RECOVERY_TIMEOUT="${RECOVERY_TIMEOUT:-900}"
RUN_LABEL="${RUN_LABEL:-local-$(date -u +%Y%m%dT%H%M%SZ)}"

if [ "$#" -gt 0 ]; then EXPERIMENTS=("$@"); else
  EXPERIMENTS=(terminate cpu_stress)
  [ "${CHAOS_LATENCY:-0}" = "1" ] && EXPERIMENTS+=(network_latency)
fi

now() { date +%s.%N; }
iso_now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(date -u +%H:%M:%S)] $*"; }

healthy_count() {
  aws elbv2 describe-target-health --target-group-arn "$TG" \
    --query "length(TargetHealthDescriptions[?TargetHealth.State=='healthy'])" --output text 2>/dev/null || echo -1
}
asg_counts() { # prints "<desired> <inservice>"
  aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG" \
    --query "AutoScalingGroups[0].[DesiredCapacity, length(Instances[?LifecycleState=='InService'])]" --output text 2>/dev/null || echo "-1 -1"
}
alarm_state() { aws cloudwatch describe-alarms --alarm-names "$1" --query 'MetricAlarms[0].StateValue' --output text 2>/dev/null || echo UNKNOWN; }
running_experiments() {
  aws fis list-experiments --query "length(experiments[?state.status=='running' || state.status=='initiating' || state.status=='pending'])" --output text 2>/dev/null || echo 0
}

wait_steady() {
  local start; start="$(date +%s)"
  while :; do
    local h d i a1 a2 a3 a4 r
    h="$(healthy_count)"; read -r d i <<< "$(asg_counts)"
    a1="$(alarm_state "$ALARM_HEALTHY")"; a2="$(alarm_state "$ALARM_CPU")"; a3="$(alarm_state "$ALARM_LAT")"; a4="$(alarm_state "$ALARM_CANARY")"
    r="$(running_experiments)"
    if [ "$h" = "$BASELINE" ] && [ "$d" = "$BASELINE" ] && [ "$i" = "$BASELINE" ] \
       && [ "$a1" != "ALARM" ] && [ "$a2" != "ALARM" ] && [ "$a3" != "ALARM" ] && [ "$a4" != "ALARM" ] && [ "$r" = "0" ]; then
      log "steady: healthy=$h desired=$d inservice=$i alarms healthy/cpu/latency/canary=$a1/$a2/$a3/$a4"
      return 0
    fi
    if [ $(( $(date +%s) - start )) -ge "$STEADY_TIMEOUT" ]; then
      log "NOT steady after ${STEADY_TIMEOUT}s: healthy=$h desired=$d inservice=$i alarms=$a1/$a2/$a3/$a4 running_fis=$r"
      return 1
    fi
    log "waiting for steady state: healthy=$h desired=$d inservice=$i alarms=$a1/$a2/$a3/$a4 running_fis=$r"
    sleep 15
  done
}

probe_loop() { # $1 = output file
  while :; do
    local t out
    t="$(now)"
    out="$(curl -s -o /dev/null -w '%{http_code} %{time_total}' --max-time 5 "$URL/healthz" 2>/dev/null || true)"
    [ -n "$out" ] || out="000 5"
    echo "$t $out" >> "$1"
    sleep "$PROBE_INTERVAL"
  done
}

sample_loop() { # $1 = output file
  while :; do
    local t h d i
    t="$(now)"; h="$(healthy_count)"; read -r d i <<< "$(asg_counts)"
    if [ "$h" != "-1" ] && [ "$d" != "-1" ]; then
      echo "{\"t\": $t, \"healthy\": $h, \"desired\": $d, \"in_service\": $i}" >> "$1"
    fi
    sleep "$SAMPLE_INTERVAL"
  done
}

BG_PIDS=()
stop_recorders() { for p in "${BG_PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; BG_PIDS=(); }
trap stop_recorders EXIT

run_experiment() {
  local name="$1" tid detect raw exp_id state t_started t_started_epoch t_done seen_alarm=0 seen_dip=0 healed_at=0 a h d i elapsed
  tid="$(o ".fis_template_ids.value.${name}")"
  case "$name" in
    terminate)       detect="$ALARM_HEALTHY" ;;
    cpu_stress)      detect="$ALARM_CPU" ;;
    network_latency) detect="$ALARM_LAT" ;;
    *) log "unknown experiment $name"; return 1 ;;
  esac
  raw="$EVID/raw/$name"; mkdir -p "$raw"; : > "$raw/probes.tsv"; : > "$raw/samples.jsonl"
  log "=== $name (template $tid, detection alarm $detect) ==="

  if ! wait_steady; then
    jq -n --arg e "$name" --arg t "$tid" '{experiment: $e, template_id: $t, error: "system never reached steady state; experiment not started", checks: [["FAIL", ($e + ": system not steady before the experiment; not started")]], passed: false}' > "$EVID/$name.json"
    echo "FAIL $name: system not steady before the experiment; not started"
    return 1
  fi

  # This function runs in a subshell (| tee); its own EXIT trap guarantees the recorders die
  # with it even if an aws call fails under set -e. Recorders never write to stdout, so a
  # leftover one could not hold the tee pipe (and the CI step) open either.
  trap stop_recorders EXIT
  probe_loop "$raw/probes.tsv" >/dev/null 2>&1 & BG_PIDS+=($!)
  sample_loop "$raw/samples.jsonl" >/dev/null 2>&1 & BG_PIDS+=($!)
  log "recording baseline for 20 s"; sleep 20

  t_started="$(iso_now)"; t_started_epoch="$(date +%s)"
  if ! exp_id="$(aws fis start-experiment --experiment-template-id "$tid" --tags "run=$RUN_LABEL,lab=08-self-healing-chaos" --query 'experiment.id' --output text)"; then
    stop_recorders
    jq -n --arg e "$name" --arg t "$tid" '{experiment: $e, template_id: $t, error: "fis start-experiment failed", checks: [["FAIL", ($e + ": fis start-experiment failed (see raw log)")]], passed: false}' > "$EVID/$name.json"
    echo "FAIL $name: fis start-experiment failed"
    return 1
  fi
  log "FIS experiment $exp_id started"

  local start; start="$(date +%s)"
  while :; do
    state="$(aws fis get-experiment --id "$exp_id" --query 'experiment.state.status' --output text 2>/dev/null || echo unknown)"
    case "$state" in completed|stopped|failed) break ;; esac
    if [ $(( $(date +%s) - start )) -ge "$EXPERIMENT_TIMEOUT" ]; then
      log "experiment still $state after ${EXPERIMENT_TIMEOUT}s: stopping it"
      aws fis stop-experiment --id "$exp_id" >/dev/null || true
    fi
    sleep 10
  done
  t_done="$(date +%s)"
  log "FIS experiment $exp_id: $state"

  # Wait for RECOVERY (the thing), with a ceiling.
  while :; do
    a="$(alarm_state "$detect")"; h="$(healthy_count)"; read -r d i <<< "$(asg_counts)"
    [ "$a" = "ALARM" ] && seen_alarm=1
    if [ "$h" != "-1" ] && [ "$h" -lt "$BASELINE" ]; then seen_dip=1; fi
    elapsed=$(( $(date +%s) - t_done ))
    case "$name" in
      terminate)
        if [ "$seen_dip" = "1" ] && [ "$h" = "$BASELINE" ] && [ "$i" = "$BASELINE" ] && [ "$healed_at" = "0" ]; then healed_at="$(date +%s)"; fi
        if [ "$healed_at" != "0" ]; then
          if [ "$seen_alarm" = "1" ] && [ "$a" = "OK" ]; then break; fi
          if [ "$seen_alarm" = "0" ] && [ $(( $(date +%s) - healed_at )) -ge 300 ]; then break; fi
        fi
        if [ "$seen_dip" = "0" ] && [ "$elapsed" -ge 240 ]; then break; fi
        ;;
      cpu_stress)
        if [ "$seen_alarm" = "1" ] && [ "$a" = "OK" ] && [ "$d" = "$BASELINE" ] && [ "$i" = "$BASELINE" ] && [ "$h" = "$BASELINE" ]; then break; fi
        if [ "$seen_alarm" = "0" ] && [ "$elapsed" -ge 600 ]; then break; fi
        ;;
      network_latency)
        if [ "$seen_alarm" = "1" ] && [ "$a" = "OK" ]; then break; fi
        if [ "$seen_alarm" = "0" ] && [ "$elapsed" -ge 420 ]; then break; fi
        ;;
    esac
    if [ "$elapsed" -ge "$RECOVERY_TIMEOUT" ]; then log "recovery wait hit ${RECOVERY_TIMEOUT}s"; break; fi
    log "$name: alarm=$a healthy=$h desired=$d inservice=$i (seen_alarm=$seen_alarm seen_dip=$seen_dip, ${elapsed}s after FIS finished)"
    sleep 10
  done
  log "recording 30 s of tail"; sleep 30
  stop_recorders

  # Collect what AWS recorded.
  aws fis get-experiment --id "$exp_id" --output json > "$raw/fis.json"
  aws cloudwatch describe-alarm-history --alarm-name "$detect" --history-item-type StateUpdate \
    --start-date "$(date -u -d "@$((t_started_epoch - 600))" +%Y-%m-%dT%H:%M:%SZ)" --max-items 100 --output json > "$raw/alarm-history.json"
  aws synthetics get-canary-runs --name "$CANARY" --max-results 100 --output json > "$raw/canary-runs.json"
  aws autoscaling describe-scaling-activities --auto-scaling-group-name "$ASG" --max-items 20 --output json > "$raw/asg-activities.json"
  aws ssm describe-automation-executions \
    --filters "Key=DocumentNamePrefix,Values=$RUNBOOK" "Key=StartTimeAfter,Values=$t_started" \
    --query 'AutomationExecutionMetadataList[].AutomationExecutionId' --output text | tr '\t' '\n' | grep -v '^None$' | grep . > "$raw/automation-ids.txt" || true
  echo '[]' > "$raw/automations.json"
  while read -r aid; do
    [ -n "$aid" ] || continue
    aws ssm get-automation-execution --automation-execution-id "$aid" --output json > "$raw/automation-$aid.json"
    jq '{id: .AutomationExecution.AutomationExecutionId, status: .AutomationExecution.AutomationExecutionStatus,
         start: .AutomationExecution.ExecutionStartTime, end: .AutomationExecution.ExecutionEndTime,
         direction: (.AutomationExecution.Parameters.Direction[0] // null), outputs: (.AutomationExecution.Outputs // {})}' \
      "$raw/automation-$aid.json" > "$raw/a.tmp"
    jq --slurpfile a "$raw/a.tmp" '. + $a' "$raw/automations.json" > "$raw/b.tmp" && mv "$raw/b.tmp" "$raw/automations.json"
    rm -f "$raw/a.tmp"
  done < "$raw/automation-ids.txt"
  jq -n --arg e "$name" --arg t "$tid" --arg x "$exp_id" --arg al "$detect" --arg s "$t_started" --arg c "$(iso_now)" \
    --argjson b "$BASELINE" --argjson m "$(o '.max_capacity.value')" \
    '{experiment: $e, template_id: $t, fis_experiment_id: $x, detect_alarm: $al, baseline: $b, max: $m,
      started_by_script_at: $s, collection_ended_at: $c}' > "$raw/meta.json"

  python3 "$HERE/measure.py" experiment "$raw" "$EVID/$name.json"
}

jq -n --arg r "$RUN_LABEL" --arg s "$(iso_now)" --arg g "$REGION" --argjson b "$BASELINE" \
  --argjson p "$PROBE_INTERVAL" --argjson q "$SAMPLE_INTERVAL" --argjson f "$(o '.fault_parameters.value')" \
  --arg rt "$(o '.canary_runtime_version.value')" \
  '{run: $r, started_at: $s, region: $g, baseline: $b, probe_interval_s: $p, sample_interval_s: $q, fault_parameters: $f, canary_runtime: $rt}' \
  > "$EVID/run.json"

overall=0
for e in "${EXPERIMENTS[@]}"; do
  run_experiment "$e" 2>&1 | tee "$EVID/raw/$e.log" || overall=1
done
python3 "$HERE/measure.py" report "$EVID" "$EVID/chaos-report.md" >/dev/null
cat "$EVID/chaos-report.md"
exit "$overall"
