# AWS DevOps Labs — automated, self-tearing-down hands-on labs

A portfolio of **real AWS infrastructure labs** defined in Terraform, with a runner
that provisions each lab, exercises it against the live AWS API to prove it works,
captures evidence, and **tears it down automatically** so idle cost stays at ~$0.

> **Honest-resume note (read this).** These are *real* labs that run on *your* AWS
> account — not simulations. The automation handles provisioning and teardown; it
> does **not** do the learning for you. Each lab's README ends with a defensible
> résumé bullet and a "be ready to explain" list. Put a bullet on your résumé only
> after you've run the lab and can explain how it works. That's what survives an
> interview. See [docs/RESUME.md](docs/RESUME.md).

## What's inside

| # | Lab | Demonstrates | Approx. cost / run |
|---|-----|--------------|--------------------|
| 01 | [VPC networking](labs/01-vpc-networking) | VPC, public/private subnets, IGW, NAT, route tables, security groups | < $0.01 (NAT GW) |
| 02 | [EC2 + ALB + Auto Scaling](labs/02-ec2-alb-asg) | High-availability web tier, load balancing, launch templates, ASG | < $0.01 (ALB + 2× t3.micro) |
| 03 | [IAM least-privilege + S3](labs/03-iam-s3-least-privilege) | IAM roles, scoped policies, policy simulator, S3 private buckets | ~$0 |
| 04 | [ECS Fargate](labs/04-ecs-fargate) | Serverless containers, task defs vs services, CloudWatch logs | < $0.01 |
| 05 | [CloudWatch monitoring](labs/05-cloudwatch-monitoring) | Metrics, alarms, SNS alerts, dashboards | ~$0 |

Every "approx. cost" assumes the run completes and tears down (a few minutes). The
runner destroys resources even if a step fails — see **Cost safety** below.

## Repo layout
```
aws-devops-labs/
├── README.md                 ← you are here
├── docs/
│   ├── SETUP.md              ← step-by-step: credentials + budget alarm (start here)
│   ├── RESUME.md            ← honest résumé bullets + interview prep
│   └── HOW-IT-WORKS.md      ← the runner internals, line by line
├── scripts/
│   ├── bootstrap.sh         ← install Terraform + AWS CLI (no root needed)
│   ├── run-lab.sh           ← THE RUNNER: apply → exercise → destroy
│   ├── run-all.sh           ← run every lab in sequence
│   ├── guardrails.sh        ← budget alarm + orphan finder + nuke
│   └── lib.sh               ← shared helpers / preflight
├── labs/
│   ├── 01-vpc-networking/   ← main.tf, variables.tf, outputs.tf, exercise.sh, README.md
│   └── … (02–05, same shape)
└── evidence/                ← per-run outputs, logs, assertions (git-ignored)
```

## Quickstart
```bash
# 1. install tooling (Terraform + AWS CLI v2 into ~/.local/bin)
scripts/bootstrap.sh

# 2. configure credentials + a budget alarm   (see docs/SETUP.md — DO THIS SAFELY)
aws configure
scripts/guardrails.sh create-budget 5        # email alert if the account passes $5/mo

# 3. dry-run a lab — shows the plan, creates NOTHING
scripts/run-lab.sh labs/01-vpc-networking --plan-only

# 4. run it for real — provisions, verifies, then destroys
scripts/run-lab.sh labs/01-vpc-networking

# run everything
scripts/run-all.sh
```

## How the runner works (the "agent")
`scripts/run-lab.sh <lab>` does, for one lab:
1. **preflight** — checks Terraform/AWS CLI/jq are present and your credentials + region work.
2. **`terraform init` + `validate`** — fail fast on config errors.
3. **`terraform apply`** — provisions the lab. Every resource is tagged
   `project=awslabs` and `run_id=<unique>` for tracking and cleanup.
4. **`exercise.sh`** — hits the live AWS API / endpoints and runs **assertions**
   (e.g. "ALB returns HTTP 200 from 2 distinct instances", "IAM policy denies
   `ec2:RunInstances`"). Output is saved as evidence.
5. **`terraform destroy`** — runs in an `EXIT` trap, so it fires **even if a step
   fails**. A failed destroy prints the exact tag-filter command to find leftovers.
6. **REPORT.md** — a per-run markdown report lands in `evidence/<lab>-<run>/`.

Flags: `--plan-only` (create nothing), `--keep` (leave it running — you pay),
`--no-destroy`.

## Cost safety
- **Auto-teardown by default.** Nothing is left running unless you pass `--keep`.
- **Budget alarm.** `scripts/guardrails.sh create-budget 5` emails you if the
  account crosses a monthly dollar cap.
- **Orphan check.** `scripts/guardrails.sh orphans` lists any lab resources still
  alive (by tag). `scripts/guardrails.sh nuke` destroys them all.
- **Tagging.** Everything carries `project=awslabs` + a unique `run_id`, so nothing
  is ever ambiguous to find or delete.

## Requirements
- Linux/macOS, `bash`, `curl`, `unzip`, `jq`
- An AWS account + an IAM identity (see [docs/SETUP.md](docs/SETUP.md) for a safe,
  least-privilege setup — **do not use your root account**)
- `scripts/bootstrap.sh` installs Terraform + AWS CLI for you (arm64 or x86_64)
