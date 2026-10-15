#!/usr/bin/env python3
"""Turn one chaos experiment's raw AWS records into measured MTTD / MTTR.

Standard library only (runs on a bare GitHub runner and on a laptop).

    measure.py experiment <raw_dir> <out.json>   one experiment -> JSON + PASS/FAIL/WARN lines
    measure.py report <evidence_dir> <out.md>      every <exp>.json in the dir -> markdown report
    measure.py selftest                            synthetic fixtures, including ones that MUST fail

Every number is computed from a timestamp an AWS service (or chaos.sh's own HTTP
probe) recorded; nothing is estimated. Definitions, all relative to t0 = the moment
FIS reports the fault action STARTED (FIS get-experiment, actions.<name>.startTime):

    time_to_detect   first transition of the experiment's detection alarm to ALARM
                     (CloudWatch alarm history) minus t0
    time_to_recover  terminate     : first 5-s target-health sample after the dip in which
                                     healthy targets are back to the baseline, minus t0
                     cpu / latency : first transition of the detection alarm back to OK
                                     after it fired, minus t0
    user impact      chaos.sh's 2-s HTTP probe of /healthz through the ALB between t0 and
                     recovery (non-200 count, longest failure streak, latency percentiles),
                     and the 1-per-minute canary runs that STARTED in the same window

Raw-dir contract (written by chaos.sh):
    meta.json                 {"experiment", "template_id", "baseline", "max", "detect_alarm",
                               "started_by_script_at", "collection_ended_at"}
    fis.json                  aws fis get-experiment
    alarm-history.json        aws cloudwatch describe-alarm-history (detection alarm, StateUpdate)
    samples.jsonl             one JSON object per 5-s sample: {"t", "healthy", "desired", "in_service"}
    probes.tsv                "<epoch> <http_code> <seconds>" per probe (code 000 = no answer)
    automations.json          [{"id", "status", "start", "end", "direction", "outputs"}]  (cpu only)
    canary-runs.json          aws synthetics get-canary-runs
"""
import json
import os
import statistics
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def ts(value):
    """AWS CLI v2 timestamps (ISO 8601, with or without offset) or epoch numbers -> epoch float."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    try:
        return float(s)
    except ValueError:
        pass
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def iso(epoch):
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def secs(a, b):
    """b - a in seconds (1 decimal), or None if either end is missing."""
    if a is None or b is None:
        return None
    return round(b - a, 1)


def load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def load_jsonl(path):
    rows = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return rows


def load_probes(path):
    rows = []
    try:
        with open(path) as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        rows.append((float(parts[0]), parts[1], float(parts[2])))
                    except ValueError:
                        pass
    except OSError:
        pass
    return rows


def pct(values, p):
    if not values:
        return None
    v = sorted(values)
    k = max(0, min(len(v) - 1, int(round(p / 100.0 * (len(v) - 1)))))
    return round(v[k] * 1000, 1)  # ms


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


def fault_window(fis):
    """(fault_start, fault_end, state, reason) from FIS get-experiment."""
    exp = fis.get("experiment", fis)
    actions = exp.get("actions") or {}
    starts = [ts(a.get("startTime")) for a in actions.values() if a.get("startTime")]
    ends = [ts(a.get("endTime")) for a in actions.values() if a.get("endTime")]
    start = min(starts) if starts else ts(exp.get("startTime"))
    end = max(ends) if ends else ts(exp.get("endTime"))
    state = (exp.get("state") or {}).get("status")
    reason = (exp.get("state") or {}).get("reason")
    return start, end, state, reason


def alarm_transitions(history):
    """[(epoch, old, new)] oldest first from describe-alarm-history (StateUpdate items)."""
    out = []
    for item in history.get("AlarmHistoryItems", []):
        if item.get("HistoryItemType") not in (None, "StateUpdate"):
            continue
        try:
            data = json.loads(item.get("HistoryData") or "{}")
        except ValueError:
            data = {}
        new = (data.get("newState") or {}).get("stateValue")
        old = (data.get("oldState") or {}).get("stateValue")
        if new:
            out.append((ts(item.get("Timestamp")), old, new))
    out.sort(key=lambda r: r[0])
    return out


def first_alarm_after(transitions, t0, slack=5.0):
    for t, _old, new in transitions:
        if new == "ALARM" and t >= t0 - slack:
            return t
    return None


def first_ok_after(transitions, t):
    if t is None:
        return None
    for tt, _old, new in transitions:
        if new == "OK" and tt > t:
            return tt
    return None


def health_dip_and_recovery(samples, t0, baseline):
    """First sample after t0 below baseline, then first sample after that back at baseline."""
    dip = None
    for s in samples:
        if s.get("t", 0) < t0:
            continue
        if dip is None and s.get("healthy", baseline) < baseline:
            dip = s["t"]
            continue
        if dip is not None and s.get("healthy", 0) >= baseline:
            return dip, s["t"], min(x.get("healthy", baseline) for x in samples if dip <= x["t"] <= s["t"])
    floor = min((x.get("healthy", baseline) for x in samples if x.get("t", 0) >= t0), default=None)
    return dip, None, floor


def probe_impact(probes, t0, t1):
    before = [p for p in probes if p[0] < t0]
    window = [p for p in probes if t0 <= p[0] <= t1] if t1 is not None else [p for p in probes if p[0] >= t0]
    bad = [p for p in window if p[1] != "200"]
    longest, streak_start, prev_bad = 0.0, None, False
    for p in window:
        if p[1] != "200":
            if not prev_bad:
                streak_start = p[0]
            longest = max(longest, p[0] - streak_start)
            prev_bad = True
        else:
            if prev_bad and streak_start is not None:
                longest = max(longest, p[0] - streak_start)  # failure lasted until this success
            prev_bad = False
    ok_lat = [p[2] for p in window if p[1] == "200"]
    base_lat = [p[2] for p in before if p[1] == "200"]
    return {
        "probes": len(window),
        "failed": len(bad),
        "availability_pct": round(100.0 * (len(window) - len(bad)) / len(window), 2) if window else None,
        "longest_failure_s": round(longest, 1),
        "latency_ms_p50": pct(ok_lat, 50),
        "latency_ms_p95": pct(ok_lat, 95),
        "latency_ms_max": round(max(ok_lat) * 1000, 1) if ok_lat else None,
        "baseline_latency_ms_p50": pct(base_lat, 50),
        "baseline_latency_ms_p95": pct(base_lat, 95),
        "status_codes": sorted({p[1] for p in bad}),
    }


def canary_impact(runs_doc, t0, t1):
    t1 = t1 if t1 is not None else float("inf")
    passed = failed = 0
    failures = []
    for run in runs_doc.get("CanaryRuns", []):
        started = ts((run.get("Timeline") or {}).get("Started"))
        if started is None or not (t0 - 60 <= started <= t1 + 60):
            continue
        state = (run.get("Status") or {}).get("State")
        if state == "PASSED":
            passed += 1
        elif state == "FAILED":
            failed += 1
            failures.append({"started": iso(started), "reason": (run.get("Status") or {}).get("StateReason")})
    return {"runs": passed + failed, "passed": passed, "failed": failed, "failures": failures[:10]}


# ---------------------------------------------------------------------------
# one experiment
# ---------------------------------------------------------------------------


def measure(raw):
    meta = load_json(os.path.join(raw, "meta.json"), {})
    fis = load_json(os.path.join(raw, "fis.json"), {})
    history = load_json(os.path.join(raw, "alarm-history.json"), {})
    samples = sorted(load_jsonl(os.path.join(raw, "samples.jsonl")), key=lambda s: s.get("t", 0))
    probes = sorted(load_probes(os.path.join(raw, "probes.tsv")))
    automations = load_json(os.path.join(raw, "automations.json"), [])
    canary_runs = load_json(os.path.join(raw, "canary-runs.json"), {})

    exp_name = meta.get("experiment", os.path.basename(raw.rstrip("/")))
    baseline = int(meta.get("baseline", 2))
    t0, t_end, fis_state, fis_reason = fault_window(fis)
    checks = []  # (level, message)

    result = {
        "experiment": exp_name,
        "fis_experiment_id": (fis.get("experiment") or {}).get("id"),
        "fis_template_id": meta.get("template_id"),
        "fis_final_state": fis_state,
        "fis_state_reason": fis_reason,
        "fault_started_at": iso(t0),
        "fault_ended_at": iso(t_end),
        "fault_duration_s": secs(t0, t_end),
        "detection_alarm": meta.get("detect_alarm"),
        "baseline_capacity": baseline,
    }
    if t0 is None:
        result["error"] = "no FIS action start time: the experiment never started"
        result["checks"] = [("FAIL", f"{exp_name}: FIS never started the fault action ({fis_state}: {fis_reason})")]
        return result

    transitions = alarm_transitions(history)
    detected = first_alarm_after(transitions, t0)
    cleared = first_ok_after(transitions, detected)
    result["detected_at"] = iso(detected)
    result["time_to_detect_s"] = secs(t0, detected)
    result["alarm_cleared_at"] = iso(cleared)

    if exp_name == "terminate":
        dip, back, floor = health_dip_and_recovery(samples, t0, baseline)
        recovered = back
        result["healthy_dip_at"] = iso(dip)
        result["healthy_floor"] = floor
        result["recovered_at"] = iso(back)
        result["recovery_signal"] = f"healthy ALB targets back to {baseline} (5-s target-health samples)"
        if dip is None:
            checks.append(("WARN", "terminate: healthy targets never dropped below the baseline in the 5-s samples (replacement faster than sampling?)"))
    else:
        recovered = cleared
        result["recovered_at"] = iso(cleared)
        result["recovery_signal"] = f"{meta.get('detect_alarm')} back to OK"
    result["time_to_recover_s"] = secs(t0, recovered)

    if exp_name == "cpu_stress":
        outs = [a for a in automations if a.get("direction") == "out"]
        ins = [a for a in automations if a.get("direction") == "in"]
        first_out = min(outs, key=lambda a: ts(a.get("start")) or 0) if outs else None
        first_in = min(ins, key=lambda a: ts(a.get("start")) or 0) if ins else None
        grew = next((s["t"] for s in samples if s.get("t", 0) >= t0 and s.get("in_service", 0) > baseline), None)
        back_to_base = None
        if grew is not None:
            back_to_base = next((s["t"] for s in samples if s["t"] > grew and s.get("desired") == baseline and s.get("in_service") == baseline), None)
        result["remediation"] = {
            "scale_out_execution": first_out,
            "scale_in_execution": first_in,
            "runbook_started_after_detect_s": secs(detected, ts(first_out.get("start"))) if first_out else None,
            "extra_instance_in_service_at": iso(grew),
            "time_to_added_capacity_s": secs(t0, grew),
            "back_to_baseline_at": iso(back_to_base),
        }
        if first_out and first_out.get("status") == "Success":
            checks.append(("PASS", f"cpu_stress: alarm -> EventBridge -> runbook scaled OUT ({(first_out.get('outputs') or {})})"))
        else:
            checks.append(("FAIL", f"cpu_stress: no successful scale-out runbook execution ({first_out})"))
        if grew is not None:
            checks.append(("PASS", f"cpu_stress: extra instance InService {secs(t0, grew)} s after the fault started"))
        else:
            checks.append(("FAIL", "cpu_stress: the ASG never had more than the baseline InService"))
        if first_in and first_in.get("status") == "Success" and back_to_base is not None:
            checks.append(("PASS", f"cpu_stress: alarm OK -> runbook scaled IN; back to {baseline} at {iso(back_to_base)}"))
        else:
            checks.append(("WARN", f"cpu_stress: scale-in back to {baseline} not observed before collection ended (runbook: {first_in})"))

    impact_end = recovered if recovered is not None else ts(meta.get("collection_ended_at"))
    result["probe_impact"] = probe_impact(probes, t0, impact_end)
    result["canary_impact"] = canary_impact(canary_runs, t0, impact_end)

    if detected is not None:
        checks.append(("PASS", f"{exp_name}: detected by {meta.get('detect_alarm')} in {secs(t0, detected)} s"))
    else:
        checks.append(("FAIL", f"{exp_name}: {meta.get('detect_alarm')} never entered ALARM after the fault"))
    if recovered is not None:
        checks.append(("PASS", f"{exp_name}: recovered in {secs(t0, recovered)} s ({result['recovery_signal']})"))
    else:
        checks.append(("FAIL", f"{exp_name}: no recovery observed before collection ended ({result['recovery_signal']})"))
    # chaos.sh never stops an experiment itself except on its own timeout, so "stopped" means either
    # the canary stop condition fired (a real outage) or the run timed out; "failed" means FIS could
    # not complete an action (e.g. SSM agent offline). Both are failures of the experiment.
    if fis_state in ("stopped", "failed"):
        checks.append(("FAIL", f"{exp_name}: FIS experiment ended '{fis_state}': {fis_reason}"))
    pi = result["probe_impact"]
    checks.append(("INFO", f"{exp_name}: user impact {pi['failed']}/{pi['probes']} probes failed, longest failure {pi['longest_failure_s']} s, "
                           f"p95 {pi['latency_ms_p95']} ms (baseline p95 {pi['baseline_latency_ms_p95']} ms); "
                           f"canary {result['canary_impact']['failed']} failed / {result['canary_impact']['runs']} runs"))
    result["checks"] = checks
    result["passed"] = not any(level == "FAIL" for level, _ in checks)
    return result


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

ORDER = ["terminate", "cpu_stress", "network_latency"]


def fmt(v, unit=""):
    return "—" if v is None else f"{v}{unit}"


def report(evid_dir):
    results = []
    for name in sorted(os.listdir(evid_dir), key=lambda n: (ORDER.index(n[:-5]) if n[:-5] in ORDER else 99, n)):
        if name.endswith(".json") and name[:-5] in ORDER:
            results.append(load_json(os.path.join(evid_dir, name), {}))
    run = load_json(os.path.join(evid_dir, "run.json"), {})
    lines = [
        "# Lab 08 chaos run — measured results",
        "",
        f"Run: {run.get('run', 'local')} · started {run.get('started_at', '?')} · region {run.get('region', '?')} · "
        f"baseline {run.get('baseline', '?')} instances · probe every {run.get('probe_interval_s', 2)} s, target health every "
        f"{run.get('sample_interval_s', 5)} s, canary every 60 s.",
        "",
        "t0 = when FIS reports the fault action started. Detect = detection alarm entered ALARM. "
        "Recover = healthy targets back to baseline (terminate) or the alarm back to OK (cpu, latency).",
        "",
        "| Experiment | Detect (s) | Recover (s) | Remediation | Probes failed | Longest failure (s) | p95 during (ms) | Canary failed/runs | Verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        pi = r.get("probe_impact") or {}
        ci = r.get("canary_impact") or {}
        if r.get("experiment") == "cpu_stress":
            rem = r.get("remediation") or {}
            remediation = f"runbook +1 instance InService at {fmt(rem.get('time_to_added_capacity_s'), ' s')}"
        elif r.get("experiment") == "terminate":
            remediation = "ASG replacement (ELB health check)"
        else:
            remediation = "detect-only"
        lines.append(
            f"| {r.get('experiment')} | {fmt(r.get('time_to_detect_s'))} | {fmt(r.get('time_to_recover_s'))} | {remediation} | "
            f"{pi.get('failed', '—')}/{pi.get('probes', '—')} | {fmt(pi.get('longest_failure_s'))} | {fmt(pi.get('latency_ms_p95'))} | "
            f"{ci.get('failed', '—')}/{ci.get('runs', '—')} | {'PASS' if r.get('passed') else 'FAIL'} |"
        )
    lines += ["", "## Checks", ""]
    for r in results:
        for level, msg in r.get("checks", []):
            lines.append(f"- **{level}** {msg}")
    lines += ["", "Raw records per experiment are in `raw/<experiment>/` next to this file; the numbers above are recomputable with "
              "`python3 measure.py experiment raw/<experiment> out.json`.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# self-test: synthetic records, including ones the measurement MUST flag
# ---------------------------------------------------------------------------


def _write(d, name, obj):
    with open(os.path.join(d, name), "w") as fh:
        if name.endswith(".jsonl"):
            fh.write("\n".join(json.dumps(o) for o in obj) + "\n")
        elif name.endswith(".tsv"):
            fh.write("\n".join(" ".join(str(x) for x in row) for row in obj) + "\n")
        else:
            json.dump(obj, fh)


def _fixture(d, exp, t0, *, healthy, alarm=None, ok=None, probes=None, automations=None, fis_state="completed", reason=None):
    os.makedirs(d, exist_ok=True)
    _write(d, "meta.json", {"experiment": exp, "template_id": "EXT1", "baseline": 2, "max": 3,
                            "detect_alarm": "a", "collection_ended_at": iso(t0 + 900)})
    _write(d, "fis.json", {"experiment": {"id": "EXP1", "state": {"status": fis_state, "reason": reason},
                                          "actions": {"x": {"startTime": iso(t0), "endTime": iso(t0 + 180)}}}})
    items = []
    if alarm is not None:
        items.append({"Timestamp": iso(t0 + alarm), "HistoryItemType": "StateUpdate",
                      "HistoryData": json.dumps({"oldState": {"stateValue": "OK"}, "newState": {"stateValue": "ALARM"}})})
    if ok is not None:
        items.append({"Timestamp": iso(t0 + ok), "HistoryItemType": "StateUpdate",
                      "HistoryData": json.dumps({"oldState": {"stateValue": "ALARM"}, "newState": {"stateValue": "OK"}})})
    _write(d, "alarm-history.json", {"AlarmHistoryItems": items})
    _write(d, "samples.jsonl", [{"t": t0 + dt, "healthy": h, "desired": des, "in_service": ins} for dt, h, des, ins in healthy])
    _write(d, "probes.tsv", probes or [(t0 + i * 2, "200", 0.004) for i in range(-10, 200)])
    _write(d, "automations.json", automations or [])
    _write(d, "canary-runs.json", {"CanaryRuns": [
        {"Status": {"State": "PASSED"}, "Timeline": {"Started": iso(t0 + 30)}},
        {"Status": {"State": "FAILED", "StateReason": "timeout"}, "Timeline": {"Started": iso(t0 + 90)}},
    ]})


def selftest():
    import tempfile

    t0 = 1_800_000_000.0
    failures = []

    def expect(name, cond, detail=""):
        print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        # 1. healthy terminate: dip at +10 s, back at +150 s, alarm at +70 s; two probe failures 4 s apart
        d = os.path.join(tmp, "terminate")
        probes = [(t0 + i * 2, "200", 0.004) for i in range(-10, 100)]
        probes[12] = (probes[12][0], "502", 0.001)
        probes[13] = (probes[13][0], "502", 0.001)
        _fixture(d, "terminate", t0, healthy=[(-5, 2, 2, 2), (5, 2, 2, 2), (10, 1, 2, 1), (60, 1, 2, 1), (150, 2, 2, 2), (155, 2, 2, 2)],
                 alarm=70, ok=200, probes=probes)
        r = measure(d)
        expect("terminate: time_to_detect = 70", r["time_to_detect_s"] == 70.0, r["time_to_detect_s"])
        expect("terminate: time_to_recover = 150 (samples, not the alarm)", r["time_to_recover_s"] == 150.0, r["time_to_recover_s"])
        expect("terminate: floor = 1", r["healthy_floor"] == 1, r["healthy_floor"])
        expect("terminate: 2 failed probes", r["probe_impact"]["failed"] == 2, r["probe_impact"])
        expect("terminate: longest failure spans to the next success (4 s)", r["probe_impact"]["longest_failure_s"] == 4.0, r["probe_impact"])
        expect("terminate: canary 1 failed of 2", r["canary_impact"]["failed"] == 1 and r["canary_impact"]["runs"] == 2, r["canary_impact"])
        expect("terminate: passed", r["passed"] is True, r["checks"])

        # 2. MUST FAIL: never recovers (healthy stays at 1)
        d = os.path.join(tmp, "never")
        _fixture(d, "terminate", t0, healthy=[(5, 2, 2, 2), (10, 1, 2, 1), (300, 1, 2, 1), (900, 1, 2, 1)], alarm=70)
        r = measure(d)
        expect("never-recovers: time_to_recover is None", r["time_to_recover_s"] is None, r["time_to_recover_s"])
        expect("never-recovers: verdict FAIL", r["passed"] is False, r["checks"])

        # 3. MUST FAIL: alarm never fires (a blind detector)
        d = os.path.join(tmp, "blind")
        _fixture(d, "terminate", t0, healthy=[(10, 1, 2, 1), (150, 2, 2, 2)])
        r = measure(d)
        expect("blind-detector: time_to_detect is None", r["time_to_detect_s"] is None)
        expect("blind-detector: verdict FAIL", r["passed"] is False, r["checks"])

        # 4. MUST NOT count an ALARM from BEFORE the fault as detection
        d = os.path.join(tmp, "stale")
        _fixture(d, "terminate", t0, healthy=[(10, 1, 2, 1), (150, 2, 2, 2)], alarm=-120)
        r = measure(d)
        expect("stale-alarm: pre-fault ALARM ignored", r["time_to_detect_s"] is None, r["time_to_detect_s"])

        # 5. cpu: alarm +100, runbook out +105 (Success), 3 in service +190, OK +260, runbook in +262, back to 2 +330
        d = os.path.join(tmp, "cpu_stress")
        autos = [
            {"id": "a1", "status": "Success", "start": iso(t0 + 105), "end": iso(t0 + 109), "direction": "out", "outputs": {"desired": "3"}},
            {"id": "a2", "status": "Success", "start": iso(t0 + 262), "end": iso(t0 + 266), "direction": "in", "outputs": {"desired": "2"}},
        ]
        _fixture(d, "cpu_stress", t0, healthy=[(5, 2, 2, 2), (110, 2, 3, 2), (190, 3, 3, 3), (270, 3, 2, 3), (330, 2, 2, 2)],
                 alarm=100, ok=260, automations=autos)
        r = measure(d)
        expect("cpu: detect 100", r["time_to_detect_s"] == 100.0, r["time_to_detect_s"])
        expect("cpu: recover = alarm OK 260", r["time_to_recover_s"] == 260.0, r["time_to_recover_s"])
        expect("cpu: runbook 5 s after detect", r["remediation"]["runbook_started_after_detect_s"] == 5.0, r["remediation"])
        expect("cpu: added capacity at 190", r["remediation"]["time_to_added_capacity_s"] == 190.0, r["remediation"])
        expect("cpu: back to baseline seen", r["remediation"]["back_to_baseline_at"] == iso(t0 + 330), r["remediation"])
        expect("cpu: passed", r["passed"] is True, r["checks"])

        # 6. MUST FAIL: alarm fired but the runbook never ran (broken EventBridge wiring)
        d = os.path.join(tmp, "cpu_nowire")
        _fixture(d, "cpu_stress", t0, healthy=[(5, 2, 2, 2), (300, 2, 2, 2)], alarm=100, ok=260)
        r = measure(d)
        expect("cpu-no-runbook: verdict FAIL", r["passed"] is False, r["checks"])

        # 7. MUST FAIL: FIS stopped by the stop condition (real outage)
        d = os.path.join(tmp, "stopped")
        _fixture(d, "terminate", t0, healthy=[(10, 1, 2, 1), (150, 2, 2, 2)], alarm=70,
                 fis_state="stopped", reason="Experiment stopped by stop condition")
        r = measure(d)
        expect("stop-condition: verdict FAIL", r["passed"] is False, r["checks"])

        # 8. MUST FAIL: experiment never started
        d = os.path.join(tmp, "nostart")
        os.makedirs(d)
        _write(d, "meta.json", {"experiment": "terminate"})
        _write(d, "fis.json", {"experiment": {"state": {"status": "failed", "reason": "role cannot be assumed"}, "actions": {}}})
        r = measure(d)
        expect("never-started: verdict FAIL", not r.get("passed", False) and r["checks"][0][0] == "FAIL", r)

        # 9. report renders every experiment present
        out = os.path.join(tmp, "evid")
        os.makedirs(out)
        for name in ("terminate", "cpu_stress"):
            with open(os.path.join(out, name + ".json"), "w") as fh:
                json.dump(measure(os.path.join(tmp, name)), fh)
        md = report(out)
        expect("report: both rows", "| terminate |" in md and "| cpu_stress |" in md, md[:400])

        # 10. ISO parsing: AWS CLI forms
        expect("ts: +00:00 form", ts("2026-10-15T12:00:00.123000+00:00") == ts("2026-10-15T12:00:00.123Z"))
        expect("ts: -07:00 offset", ts("2026-10-15T05:00:00-07:00") == ts("2026-10-15T12:00:00Z"))

    print(f"selftest: {'FAILED ' + str(len(failures)) if failures else 'all passed'}")
    return 1 if failures else 0


def main(argv):
    if len(argv) >= 2 and argv[1] == "selftest":
        return selftest()
    if len(argv) == 4 and argv[1] == "experiment":
        r = measure(argv[2])
        with open(argv[3], "w") as fh:
            json.dump(r, fh, indent=2, default=str)
        for level, msg in r.get("checks", []):
            print(f"{level} {msg}")
        return 0 if r.get("passed") else 1
    if len(argv) == 4 and argv[1] == "report":
        md = report(argv[2])
        with open(argv[3], "w") as fh:
            fh.write(md)
        print(md)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
