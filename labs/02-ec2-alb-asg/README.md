# Lab 02 — EC2 + ALB + Auto Scaling Group (a highly-available web tier)

**What it builds:** an internet-facing web tier that survives an instance (or AZ)
failure — an Application Load Balancer spreading traffic across an Auto Scaling
Group of EC2 instances in two Availability Zones.

```
                         Internet
                            │
                     ┌──────┴───────┐
                     │ Internet GW  │
                     └──────┬───────┘
                ┌───────────┴────────────┐
                │  Application Load       │   (internet-facing, :80)
                │  Balancer  + TargetGrp  │   ALB SG: 80 from 0.0.0.0/0
                └─────┬───────────────┬───┘
            ┌─────────┴───┐     ┌─────┴─────────┐
       AZ-a │ public /24  │     │ public /24    │ AZ-b
            │  ┌────────┐ │     │  ┌────────┐   │
            │  │  EC2   │ │     │  │  EC2   │   │   instance SG:
            │  │ httpd  │ │     │  │ httpd  │   │   80 ONLY from ALB SG
            │  └────────┘ │     │  └────────┘   │
            └─────────────┘     └───────────────┘
                  └── Auto Scaling Group (min=max=desired=2) ──┘
```

## Concepts demonstrated
- **Application Load Balancer** — L7 load balancing, listener on :80 forwarding to
  a **target group** with an HTTP health check (`/`)
- **Auto Scaling Group** across two AZs — fixed capacity (2) here, but the same
  resource scales and self-heals (unhealthy instances are replaced)
- **Launch Template** — immutable instance blueprint; AMI resolved from the public
  **SSM parameter** for "latest Amazon Linux 2023" (no hard-coded AMI ids)
- **IMDSv2** — `http_tokens = required`; user_data fetches the instance-id with a
  token so each page proves *which* instance answered (load balancing is visible)
- **Least-privilege security groups** — the instances accept :80 **only** from the
  ALB's security group (`source_security_group_id`), never from the internet directly
- **No NAT Gateway** — instances live in public subnets to keep the lab cheap; the
  SG, not a public route, is what keeps them safe
- **Default tags** for cost tracking and clean teardown

## Run it
```bash
scripts/run-lab.sh labs/02-ec2-alb-asg             # apply → verify → destroy
scripts/run-lab.sh labs/02-ec2-alb-asg --plan-only # see the plan, create nothing
```

## What the runner verifies (evidence)
`exercise.sh` polls the ALB and **asserts**:
- `http://<alb_dns_name>/` returns **HTTP 200** (retries ~2 min while targets
  register healthy) — hard failure if it never does
- sampling the ALB 8 times returns **≥2 distinct instance ids** (PASS), or 1 (WARN)

Evidence (ASG state, target health, response headers/body, observed instance ids,
pass/fail) lands in `evidence/02-ec2-alb-asg-<run>/`.

## Cost
ALB is the only notable charge (~$0.0225/hr + tiny LCU) plus 2× `t3.micro`
(~$0.0104/hr each). A full apply→destroy cycle runs in a few minutes, so a run
costs **pennies**. Nothing is left running.

## Résumé bullet (defensible — make sure you can explain every word)
> Built a highly-available, self-healing web tier on AWS with Terraform — an
> internet-facing Application Load Balancer fronting an Auto Scaling Group of EC2
> instances across two Availability Zones, with least-privilege security groups and
> IMDSv2; automated a runner that provisions, verifies load balancing and health via
> the AWS API and live HTTP, then tears the environment down for zero idle cost.

**Be ready to explain:** the difference between an ALB target group and the ASG,
why the instance SG references the ALB's SG instead of a CIDR, how a health check
plus `health_check_type = "ELB"` makes the tier self-heal, and why IMDSv2 (token
required) matters.
