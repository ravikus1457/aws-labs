# Lab 08 chaos run — measured results

Run: gha-38814313911 · started 2026-10-16T00:42:02Z · region us-east-1 · baseline 2 instances · probe every 2 s, target health every 5 s, canary every 60 s.

t0 = when FIS reports the fault action started. Detect = detection alarm entered ALARM. Recover = healthy targets back to baseline (terminate) or the alarm back to OK (cpu, latency).

| Experiment | Detect (s) | Recover (s) | Remediation | Probes failed | Longest failure (s) | p95 during (ms) | Canary failed/runs | Verdict |
|---|---|---|---|---|---|---|---|---|
| terminate | 131.4 | 60.9 | ASG replacement (ELB health check) | 1/29 | 2.1 | 130.7 | 0/3 | PASS |
| cpu_stress | 140.6 | 320.6 | runbook +1 instance InService at 166.6 s | 0/151 | 0.0 | 134.8 | 0/7 | PASS |

## Checks

- **PASS** terminate: detected by awslabs-lab08-healthy-hosts-low in 131.4 s
- **PASS** terminate: recovered in 60.9 s (healthy ALB targets back to 2 (5-s target-health samples))
- **INFO** terminate: user impact 1/29 probes failed, longest failure 2.1 s, p95 130.7 ms (baseline p95 130.4 ms); canary 0 failed / 3 runs
- **PASS** cpu_stress: alarm -> EventBridge -> runbook scaled OUT ({'Scale.Result': ['{"direction":"out","previous":"2","desired":"3","action":"set_desired_capacity"}']})
- **PASS** cpu_stress: extra instance InService 166.6 s after the fault started
- **PASS** cpu_stress: alarm OK -> runbook scaled IN; back to 2 at 2026-10-16T00:52:53Z
- **PASS** cpu_stress: detected by awslabs-lab08-cpu-high in 140.6 s
- **PASS** cpu_stress: recovered in 320.6 s (awslabs-lab08-cpu-high back to OK)
- **INFO** cpu_stress: user impact 0/151 probes failed, longest failure 0.0 s, p95 134.8 ms (baseline p95 130.0 ms); canary 0 failed / 7 runs

Raw records per experiment are in `raw/<experiment>/` next to this file; the numbers above are recomputable with `python3 measure.py experiment raw/<experiment> out.json`.
