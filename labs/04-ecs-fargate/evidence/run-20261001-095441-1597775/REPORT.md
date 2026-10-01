# Evidence report — 04-ecs-fargate

- **Run ID:** `r20261001-095441-1597775`
- **Account:** `<account-id>`
- **Region:** `us-east-1`
- **Date (UTC):** 2026-10-01 16:55:58

## Terraform outputs
```json
{
  "cluster_name": {
    "sensitive": false,
    "type": "string",
    "value": "awslabs-r20261001-095441-1597775-cluster"
  },
  "security_group_id": {
    "sensitive": false,
    "type": "string",
    "value": "sg-047eb3e5418e020b0"
  },
  "service_name": {
    "sensitive": false,
    "type": "string",
    "value": "awslabs-r20261001-095441-1597775-svc"
  },
  "subnet_ids": {
    "sensitive": false,
    "type": [
      "tuple",
      [
        "string",
        "string"
      ]
    ],
    "value": [
      "subnet-09534b1ff414f0801",
      "subnet-0a8d16309f7fc1ba6"
    ]
  },
  "task_family": {
    "sensitive": false,
    "type": "string",
    "value": "awslabs-r20261001-095441-1597775-web"
  },
  "vpc_id": {
    "sensitive": false,
    "type": "string",
    "value": "vpc-0181b8a855440c2ae"
  }
}
```

## Scenario exercise log
```
Verifying ECS Fargate service awslabs-r20261001-095441-1597775-svc on cluster awslabs-r20261001-095441-1597775-cluster in us-east-1
--- Waiting for a RUNNING task ---
  attempt 1/30: no running task yet, retrying in 5s...
  attempt 2/30: no running task yet, retrying in 5s...
RUNNING task: arn:aws:ecs:us-east-1:<account-id>:task/awslabs-r20261001-095441-1597775-cluster/d896f6b2e2cb42558dabb0362f2ab7fd
--- Resolving public IP ---
ENI: eni-048c5249b553ca64c
Public IP: 34.227.25.54
--- Polling http://34.227.25.54/ ---
  attempt 1/24: got HTTP 000000, retrying in 5s...
  attempt 2/24: got HTTP 000000, retrying in 5s...
HTTP 200 from container
final HTTP code: 200
--- Service summary ---
{
    "Service": "awslabs-r20261001-095441-1597775-svc",
    "Status": "ACTIVE",
    "Desired": 1,
    "Running": 1
}
--- Assertions ---
PASS: container reachable over HTTP (200)
PASS: service runningCount >= 1 (1)
```
