# Putting this on your résumé — honestly

Done right, this project is strong résumé material: it shows Terraform, core AWS
services, automation, and cost-awareness — exactly what Cloud/DevOps hiring looks
for. Done wrong (listing skills you can't explain), it backfires in the first
interview. Here's how to do it right.

## The rule
List a bullet only after you've (a) **run the lab**, (b) **read its README**, and
(c) can **explain how it works without notes**. The runner automates the *toil*
(provisioning/teardown), not the *understanding*.

## One project bullet (top of a "Projects" section)
> **AWS DevOps Labs** — Built a Terraform-based lab platform with a Bash runner that
> provisions, validates against the live AWS API, captures evidence, and
> auto-destroys each environment for repeatable, near-zero-cost practice. Covers
> VPC networking, HA compute (ALB + Auto Scaling), IAM least-privilege, ECS Fargate,
> and CloudWatch observability. *(Terraform, AWS, Bash, CI-style automation)*

## Per-lab bullets (pick the ones you can defend)
- **Networking:** Designed a multi-AZ VPC (public/private subnets, IGW, NAT, route
  tables, security groups) in Terraform with automated routing verification.
- **High availability:** Deployed an HA web tier — Auto Scaling Group of EC2 behind
  an Application Load Balancer — and verified load distribution across instances.
- **Security:** Implemented least-privilege IAM roles/policies and private S3
  buckets; proved enforcement with the IAM Policy Simulator (allow PutObject, deny
  RunInstances).
- **Containers:** Ran containers on ECS Fargate (task definitions, services, task
  execution roles, CloudWatch logging) with automated health verification.
- **Observability:** Built CloudWatch metric alarms, dashboards, and SNS alerting
  with automated alarm-state testing.

## Interview prep — be ready for these
- Why do private subnets use a **NAT Gateway** instead of an Internet Gateway?
- What's the difference between a **security group** and a **NACL**?
- **ALB vs NLB**? What does a target group health check do?
- Why **IAM roles** over long-lived access keys? **Implicit** vs **explicit** deny?
- **Fargate vs EC2** launch type — when would you pick each?
- What's an alarm **evaluation period**, and what are the three alarm states?
- How does your runner keep **costs** down, and what happens if a destroy fails?

## What NOT to claim
- Don't imply production scale or real traffic — these are labs.
- Don't list a service you only saw in a diagram. If you didn't run lab 04, don't
  claim ECS.
- Don't say "managed AWS infrastructure" if you mean "built personal labs." Frame it
  as a **portfolio/learning project** — that's honest and still valued.
