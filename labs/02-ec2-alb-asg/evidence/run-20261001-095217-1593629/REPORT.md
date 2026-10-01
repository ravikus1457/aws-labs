# Evidence report — 02-ec2-alb-asg

- **Run ID:** `r20261001-095217-1593629`
- **Account:** `<account-id>`
- **Region:** `us-east-1`
- **Date (UTC):** 2026-10-01 16:56:40

## Terraform outputs
```json
{
  "alb_dns_name": {
    "sensitive": false,
    "type": "string",
    "value": "awslabs--1593629-alb-1184158402.us-east-1.elb.amazonaws.com"
  },
  "asg_name": {
    "sensitive": false,
    "type": "string",
    "value": "awslabs-r20261001-095217-1593629-asg"
  },
  "target_group_arn": {
    "sensitive": false,
    "type": "string",
    "value": "arn:aws:elasticloadbalancing:us-east-1:<account-id>:targetgroup/awslabs--1593629-tg/f0fdf2df062b2b64"
  },
  "vpc_id": {
    "sensitive": false,
    "type": "string",
    "value": "vpc-0a9bdf7d93df80456"
  }
}
```

## Scenario exercise log
```
Exercising web tier behind ALB: awslabs--1593629-alb-1184158402.us-east-1.elb.amazonaws.com in us-east-1
--- Auto Scaling Group ---
{
    "Name": "awslabs-r20261001-095217-1593629-asg",
    "Min": 2,
    "Max": 2,
    "Desired": 2,
    "Instances": [
        {
            "Id": "i-05d9c74c35b2e87a7",
            "AZ": "us-east-1b",
            "Health": "Healthy",
            "State": "InService"
        },
        {
            "Id": "i-0781270b304bc5b8a",
            "AZ": "us-east-1a",
            "Health": "Healthy",
            "State": "InService"
        }
    ]
}
--- Target group health ---
--------------------------------------
|        DescribeTargetHealth        |
+------------+-----------------------+
|    State   |        Target         |
+------------+-----------------------+
|  unhealthy |  i-0781270b304bc5b8a  |
|  initial   |  i-05d9c74c35b2e87a7  |
+------------+-----------------------+
--- Polling http://awslabs--1593629-alb-1184158402.us-east-1.elb.amazonaws.com/ for HTTP 200 (up to ~120s) ---
attempt 1: HTTP 502
attempt 2: HTTP 502
attempt 3: HTTP 502
attempt 4: HTTP 502
attempt 5: HTTP 200
first HTTP code that returned 200: 200
--- Sampling the ALB 8 times to observe load balancing ---
distinct instance ids observed: 2
i-05d9c74c35b2e87a7
i-0781270b304bc5b8a
--- Assertions ---
PASS: ALB served HTTP 200
PASS: load balanced across 2 distinct instances
```
