# Lab 03 — IAM Least Privilege vs S3

**What it builds:** an IAM role with a hand-scoped, least-privilege policy that
can do *exactly* what an app needs against one private S3 bucket — and provably
nothing more.

```
   ┌──────────────────┐   sts:AssumeRole    ┌────────────────────────┐
   │  This AWS account │ ─────────────────▶ │  IAM role              │
   │  (trust policy)   │                     │  awslabs-<run>-app-role│
   └──────────────────┘                     └───────────┬────────────┘
                                                         │ attached
                                              ┌──────────▼───────────┐
                                              │ IAM policy (ALLOW)    │
                                              │  s3:GetObject  ─┐     │
                                              │  s3:PutObject   │ /*  │
                                              │  s3:ListBucket  ┘     │
                                              └──────────┬───────────┘
                                                         │ only
                                              ┌──────────▼───────────┐
                                              │ S3 bucket            │
                                              │  …-data  (PRIVATE)    │
                                              │  • public access OFF  │
                                              │  • versioning ON      │
                                              └──────────────────────┘
   Everything else (DeleteBucket, EC2, other buckets) → implicit DENY
```

## Concepts demonstrated
- **Least privilege** — the policy grants only `GetObject`/`PutObject` on objects
  and `ListBucket` on the bucket. No wildcards on actions, no other resources.
- **IAM roles vs users** — a role is assumed for temporary credentials; there are
  no long-lived access keys to leak or rotate.
- **Trust policy** — the role's `assume_role_policy` scopes *who* may assume it to
  the current account only (`…:root`), via `aws_caller_identity`.
- **IAM Policy Simulator** — proves what an identity can/can't do *without* making
  real calls or needing extra permissions (`iam:SimulatePrincipalPolicy`).
- **S3 public access block** — all four settings on, so the bucket can never be
  made public by ACL or bucket policy.
- **Bucket versioning** — object history is retained for recovery.
- **`force_destroy`** — lets the automated runner tear the bucket down cleanly
  even if it contains objects.

## Run it
```bash
scripts/run-lab.sh labs/03-iam-s3-least-privilege            # apply → verify → destroy
scripts/run-lab.sh labs/03-iam-s3-least-privilege --plan-only # see the plan, create nothing
```

## What the runner verifies (evidence)
`exercise.sh` calls the AWS API and **asserts**:
- the role **is allowed** `s3:GetObject` and `s3:PutObject` on `…/test.txt`
  (Policy Simulator → both `allowed`)
- the role **is denied** `s3:DeleteBucket` and `ec2:RunInstances`
  (Policy Simulator → both `implicitDeny`) — least privilege enforced
- the bucket's public-access-block has **all four settings true** — bucket is private

Evidence (simulator results, public-access-block, pass/fail) lands in
`evidence/03-iam-s3-least-privilege-<run>/`.

## Cost
**≈ $0.** IAM roles and policies are free, and an empty S3 bucket costs nothing.
The Policy Simulator is free. A full apply→verify→destroy cycle costs nothing.
Nothing is left running.

## Résumé bullet (defensible — make sure you can explain every word)
> Implemented least-privilege AWS IAM with Terraform: an application role with a
> tightly scoped S3 policy (object read/write + list, nothing else) and an
> account-scoped trust policy, then automated proof of the access boundary using
> the IAM Policy Simulator to assert both granted and denied actions.

**Be ready to explain:** what *least privilege* means and why it limits blast
radius; why you prefer assumable roles over long-lived user access keys; and the
difference between an **implicit deny** (no statement allows it) and an
**explicit deny** (a `Deny` statement that overrides any allow).
