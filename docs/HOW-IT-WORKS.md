# How it works — the runner, step by step

This is the "breakdown" of what actually happens when you run a lab. The runner is
`scripts/run-lab.sh`; shared helpers live in `scripts/lib.sh`.

## The lifecycle of one lab

```
 you: scripts/run-lab.sh labs/01-vpc-networking
        │
        ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ 1. preflight        terraform? aws? jq? creds valid? region? │
 │ 2. run id           r20260626-120000-1234  (time + pid)      │
 │ 3. evidence dir     evidence/01-vpc-networking-<run>/        │
 │ 4. terraform init   download AWS provider, set up state      │
 │ 5. terraform validate                                        │
 │ 6. terraform apply  ← resources created, tagged run_id       │
 │ 7. terraform output -json  → outputs.json                    │
 │ 8. exercise.sh      hit live AWS API, run ASSERTIONS         │
 │ 9. REPORT.md        human-readable evidence report           │
 │10. terraform destroy  ← runs in an EXIT trap (always)        │
 └─────────────────────────────────────────────────────────────┘
```

## Why each piece exists

**1. preflight (`lib.sh: preflight`)** — fails *before* touching AWS if a tool is
missing or credentials are bad, so you never get half-created infra from a typo.
It resolves the region from `$AWS_REGION`, `$AWS_DEFAULT_REGION`, or `aws configure`.

**2. run id (`lib.sh: new_run_id`)** — `r<date>-<pid>`. Deterministic enough to be
readable, unique enough that two runs never collide. It becomes the `run_id` tag on
every resource, so cleanup is always unambiguous.

**3. tagging** — the runner exports `TF_VAR_run_id`, `TF_VAR_project`,
`TF_VAR_aws_region`. Each lab's `provider "aws"` block sets `default_tags`, so
*every* resource is tagged without per-resource effort. That powers
`guardrails.sh orphans` and `nuke`.

**4–7. Terraform** — standard `init → validate → apply → output`. State is local
(`terraform.tfstate` in the lab dir), which is fine for ephemeral labs. Outputs are
dumped to `outputs.json` so the exercise step doesn't need to parse Terraform.

**8. exercise.sh (the proof)** — this is what makes the labs *evidence*, not just
"it applied". Each one calls the live AWS API and asserts real behavior:
- Lab 01: route tables actually route public→IGW and private→NAT
- Lab 02: the ALB returns HTTP 200 from *2 distinct* instances (load balancing works)
- Lab 03: the IAM policy *allows* `s3:PutObject` and *denies* `ec2:RunInstances`
- Lab 04: the Fargate task serves HTTP 200 on its public IP
- Lab 05: the alarm transitions to `ALARM` when the metric breaches threshold

Each exercise prints `PASS`/`FAIL` lines and exits non-zero on failure. The contract:
it receives the evidence dir as `$1` and reads outputs from `$OUTPUTS_JSON`.

**10. teardown (the EXIT trap)** — registered with `trap cleanup EXIT` *before*
apply, so `terraform destroy` runs whether the lab succeeds, fails, or you Ctrl-C.
This is the cost guarantee. If destroy itself fails, it prints the tag-filter
command to find and kill the leftovers.

## Adding your own lab
1. `mkdir labs/06-my-thing`
2. Copy the `terraform{}` + `provider "aws"` (with `default_tags`) block and the
   `aws_region`/`project`/`run_id` variables from any existing lab.
3. Write `main.tf` / `outputs.tf`, then an `exercise.sh` that reads `$OUTPUTS_JSON`,
   does something real, and asserts it.
4. `scripts/run-lab.sh labs/06-my-thing --plan-only` to check, then run it.

The runner discovers labs by directory, so `run-all.sh` picks up new ones automatically.
