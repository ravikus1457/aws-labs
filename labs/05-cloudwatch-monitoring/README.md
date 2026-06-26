# Lab 05 — CloudWatch Monitoring & Alerting

**What it builds:** an observability slice — a custom metric driving a
CloudWatch **alarm** that notifies an **SNS topic**, plus a **dashboard** to
visualise the signal. No billable infrastructure needed: we publish a custom
metric ourselves and drive the alarm.

```
   put-metric-data
   AwsLabs/Demo / DemoLoad
   (Service=<project>-<run>)
            │
            ▼
   ┌─────────────────┐  threshold > 70    ┌──────────────┐
   │ CloudWatch      │ ─────────────────▶ │  SNS topic   │
   │ Metric Alarm    │  alarm_actions     │  *-alerts    │
   │ *-cpu-high      │                    └──────────────┘
   └────────┬────────┘
            │ plotted on
            ▼
   ┌─────────────────┐
   │ CloudWatch      │
   │ Dashboard       │
   │ *-dashboard     │
   └─────────────────┘
```

## Concepts demonstrated
- **CloudWatch metrics** — including **custom metrics** published via
  `put-metric-data` (namespace `AwsLabs/Demo`, metric `DemoLoad`, dimension
  `Service`)
- **Metric alarms** — `comparison_operator`, `threshold`, `statistic`,
  `period`, and **evaluation periods** (how many periods must breach before the
  alarm changes state)
- **Alarm states** — `OK`, `ALARM`, `INSUFFICIENT_DATA`
- **`treat_missing_data`** — set to `notBreaching` so gaps in the custom metric
  don't flap the alarm into `INSUFFICIENT_DATA`
- **SNS notifications** — `alarm_actions` / `ok_actions` wire the alarm to a
  topic (the integration point for email/Slack/PagerDuty)
- **Dashboards** — a `metric` widget (`dashboard_body = jsonencode(...)`) with a
  threshold annotation
- **`set-alarm-state`** — the deterministic way to test alarm wiring and actions
  without waiting for real datapoints to evaluate
- **Default tags** for cost tracking and clean teardown

## Run it
```bash
scripts/run-lab.sh labs/05-cloudwatch-monitoring             # apply → verify → destroy
scripts/run-lab.sh labs/05-cloudwatch-monitoring --plan-only # see the plan, create nothing
```

## What the runner verifies (evidence)
`exercise.sh` calls the AWS API and **asserts**:
- the SNS topic and dashboard exist
- pushes a breaching datapoint (`DemoLoad=95`) to the custom metric
- forces the alarm to `ALARM` with `set-alarm-state` (deterministic), then
  re-reads it and asserts **`StateValue == "ALARM"`** → *alarm fired on
  threshold breach*

Evidence (SNS topic, before/after alarm state, dashboard, pass/fail) lands in
`evidence/05-cloudwatch-monitoring-<run>/`. The alarm is reset to `OK` at the end.

## Cost
Essentially **$0** for a short run. CloudWatch alarms are ~$0.10/alarm/month
(prorated to a fraction of a cent for a few-minute run), custom metrics and
dashboards in this volume are within/near the free tier, and SNS publishes are
negligible. Nothing billable is left running after teardown.

## Résumé bullet (defensible — make sure you can explain every word)
> Built CloudWatch observability with Terraform: a threshold alarm on a custom
> metric wired to an SNS topic for alerting, plus a dashboard; automated an
> end-to-end test that publishes a breaching datapoint and asserts the alarm
> transitions to ALARM via the AWS API, then tears everything down for
> zero-idle-cost, repeatable runs.

**Be ready to explain:** what an alarm **evaluation period** is (how many
consecutive periods must breach the threshold before the state changes), the
three alarm states (`OK` / `ALARM` / `INSUFFICIENT_DATA`) and what
`treat_missing_data` does, why the alarm publishes to **SNS** instead of acting
directly (decoupling: one topic fans out to many subscribers), and why
`set-alarm-state` is used to test alarm wiring deterministically.
