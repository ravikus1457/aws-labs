# Evidence report — 05-cloudwatch-monitoring

- **Run ID:** `r20261001-094135-1576915`
- **Account:** `<account-id>`
- **Region:** `us-east-1`
- **Date (UTC):** 2026-10-01 16:45:00

## Terraform outputs
```json
{
  "alarm_name": {
    "sensitive": false,
    "type": "string",
    "value": "awslabs-r20261001-094135-1576915-cpu-high"
  },
  "dashboard_name": {
    "sensitive": false,
    "type": "string",
    "value": "awslabs-r20261001-094135-1576915-dashboard"
  },
  "service_dimension": {
    "sensitive": false,
    "type": "string",
    "value": "awslabs-r20261001-094135-1576915"
  },
  "sns_topic_arn": {
    "sensitive": false,
    "type": "string",
    "value": "arn:aws:sns:us-east-1:<account-id>:awslabs-r20261001-094135-1576915-alerts"
  }
}
```

## Scenario exercise log
```
Exercising CloudWatch alarm 'awslabs-r20261001-094135-1576915-cpu-high' (topic arn:aws:sns:us-east-1:<account-id>:awslabs-r20261001-094135-1576915-alerts) in us-east-1
--- SNS topic ---
--------------------------------------------------------------------------------------------
|                                    GetTopicAttributes                                    |
+----------+-------------------------------------------------------------------------------+
|  Owner   |  <account-id>                                                                 |
|  TopicArn|  arn:aws:sns:us-east-1:<account-id>:awslabs-r20261001-094135-1576915-alerts   |
+----------+-------------------------------------------------------------------------------+
--- Initial alarm state (expect OK or INSUFFICIENT_DATA) ---
{
    "Name": "awslabs-r20261001-094135-1576915-cpu-high",
    "State": "INSUFFICIENT_DATA",
    "Threshold": 70.0,
    "Metric": "DemoLoad"
}
--- Push a breaching datapoint to the custom metric (real-world path) ---
pushed DemoLoad=95 for Service=awslabs-r20261001-094135-1576915
--- Force alarm into ALARM state (deterministic) ---
--- Alarm state after breach ---
{
    "Name": "awslabs-r20261001-094135-1576915-cpu-high",
    "State": "ALARM",
    "Reason": "lab exercise: simulated high load"
}
--- Dashboard ---
awslabs-r20261001-094135-1576915-dashboard
--- Assertions ---
PASS: alarm fired on threshold breach (StateValue=ALARM)
--- Reset alarm state to OK ---
```
