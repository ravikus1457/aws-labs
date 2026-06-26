# Lab 04 — ECS Fargate (serverless containers)

**What it builds:** a single nginx container running on **AWS Fargate** — no EC2
instances to manage — reachable over the internet, streaming its logs to
CloudWatch.

```
            Internet
               │  HTTP :80
        ┌──────┴───────┐
        │ Internet GW  │
        └──────┬───────┘
        ┌──────┴───────────────────────────┐
        │  VPC 10.40.0.0/16                 │
        │  ┌─────────────────────────────┐  │
        │  │ Security Group (80 in)      │  │
        │  │  ┌───────────────────────┐  │  │
        │  │  │  Fargate task         │  │  │
   AZ-a │  │  │  nginx:stable  :80    │  │  │
        │  │  │  public subnet + EIP  │  │  │
        │  │  └──────────┬────────────┘  │  │
        │  └─────────────┼───────────────┘  │
        └────────────────┼──────────────────┘
                         │ stdout/stderr (awslogs)
                  ┌──────┴────────┐
                  │  CloudWatch   │  /ecs/<name>
                  │  Logs (1 day) │
                  └───────────────┘
```

## Concepts demonstrated
- **ECS launch types — Fargate vs EC2.** Fargate is *serverless*: you declare
  CPU/memory and AWS runs the container; there are no instances to patch, scale,
  or pay for when idle. The EC2 launch type makes you own the host fleet.
- **Task definition vs service.** The *task definition* is the immutable
  blueprint (image, CPU/memory, ports, logging). The *service* is the controller
  that keeps `desired_count` tasks running and replaces failed ones.
- **Task execution role.** A role the ECS *agent* assumes (not your app code) to
  pull the image and write logs. Trusts `ecs-tasks.amazonaws.com` and carries the
  managed `AmazonECSTaskExecutionRolePolicy`.
- **awsvpc networking.** Each Fargate task gets its own ENI and private/public IP,
  so it behaves like a first-class VPC citizen with its own security group.
- **awslogs / CloudWatch logging.** Container stdout/stderr is shipped to a
  CloudWatch log group with a 1-day retention so it stays cheap.
- **No NAT.** The task runs in a public subnet with `assign_public_ip = true`, so
  it can pull the public image and be reached without a (costly) NAT Gateway.

## Run it
```bash
scripts/run-lab.sh labs/04-ecs-fargate            # apply → verify → destroy
scripts/run-lab.sh labs/04-ecs-fargate --plan-only # see the plan, create nothing
```

## What the runner verifies (evidence)
`exercise.sh` calls the AWS API and **asserts**:
- the service schedules a **RUNNING** task, whose ENI resolves to a **public IP**
- `curl http://<public-ip>/` returns **HTTP 200** (the nginx welcome page) →
  *"container reachable"*
- `describe-services` reports **runningCount ≥ 1**

Evidence (task ARN, public IP, HTTP response, service summary, pass/fail) lands in
`evidence/04-ecs-fargate-<run>/`.

## Cost
Fargate bills **per-second** for the CPU/memory you reserve — here 0.25 vCPU /
0.5 GB. A few-minute apply→verify→destroy cycle costs **a fraction of a cent**;
CloudWatch logs retain for 1 day and there is no NAT Gateway. Nothing is left
running.

## Résumé bullet (defensible — make sure you can explain every word)
> Deployed a containerized service on AWS ECS Fargate with Terraform — own VPC,
> task definition, IAM task-execution role, security group, and CloudWatch
> logging — and built a runner that provisions, verifies the container is live via
> the AWS API and an HTTP health check, and tears everything down for repeatable,
> zero-idle-cost testing.

**Be ready to explain:** the difference between the Fargate and EC2 launch types,
the difference between a task definition and a service, and why a *task execution
role* exists (the ECS agent pulls images and writes logs on your behalf — separate
from any task role your app would use to call other AWS services).
