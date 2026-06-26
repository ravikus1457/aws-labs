# Setup — credentials & cost safety (do this once)

This is the careful part. Follow it in order. **Never put your AWS root account
credentials into the CLI**, and **never paste secret keys into a chat window** —
type them only into your own terminal.

---

## Step 1 — Install the tooling
```bash
scripts/bootstrap.sh
```
Installs Terraform + AWS CLI v2 into `~/.local/bin` (no root). If `~/.local/bin`
isn't on your `PATH`, add this to `~/.bashrc`:
```bash
export PATH="$HOME/.local/bin:$PATH"
```
Verify:
```bash
terraform version && aws --version && jq --version
```

---

## Step 2 — Create a dedicated IAM user (NOT root)

Using your AWS root login *only for this one-time setup*:

1. Open the **IAM** console → **Users** → **Create user**. Name it e.g. `labs-admin`.
2. **Permissions:** for a learning account, attach the AWS managed policy
   **`AdministratorAccess`** (simplest; the labs touch many services). If you want
   tighter scope, attach instead: `AmazonVPCFullAccess`, `AmazonEC2FullAccess`,
   `IAMFullAccess`, `AmazonS3FullAccess`, `AmazonECS_FullAccess`,
   `CloudWatchFullAccess`, `AmazonSNSFullAccess`, and `AWSBudgetsActionsWithAWSResourceControlAccess`.
3. Create the user, then open it → **Security credentials** → **Create access key**
   → choose **Command Line Interface (CLI)**. Copy the **Access key ID** and
   **Secret access key**.

> Treat the secret key like a password. If it ever leaks, deactivate it in IAM.

---

## Step 3 — Configure credentials (in YOUR terminal)
```bash
aws configure
```
Paste when prompted:
- **AWS Access Key ID:** *(from step 2)*
- **AWS Secret Access Key:** *(from step 2)*
- **Default region name:** `us-east-1` *(or your nearest region)*
- **Default output format:** `json`

> Inside a Claude Code session you can run this by typing `! aws configure` in the
> prompt — it runs in *your* shell. Still, prefer plain terminal so the secret
> never lands in a transcript. The key is stored in `~/.aws/credentials`, which is
> already covered by this repo's `.gitignore` patterns — but it lives outside the
> repo anyway.

Confirm it works:
```bash
aws sts get-caller-identity
```
You should see your account id and the `labs-admin` user ARN.

---

## Step 4 — Set a budget alarm (so you can't be surprised)
```bash
scripts/guardrails.sh create-budget 5
```
Creates an AWS Budget that **emails you** when the account's actual spend crosses
80% and 100% of **$5/month**. Change `5` to whatever ceiling you want. You'll be
prompted for an email (or set `BUDGET_EMAIL` first).

> Budgets are free. This is your safety net in case a `--keep` run is forgotten or
> a destroy ever fails.

---

## Step 5 — First run (safe dry-run, then real)
```bash
# creates NOTHING — just shows the Terraform plan
scripts/run-lab.sh labs/01-vpc-networking --plan-only

# real run: provision → verify → auto-destroy
scripts/run-lab.sh labs/01-vpc-networking
```
Watch it apply, run assertions, then destroy. Check `evidence/01-vpc-networking-*/REPORT.md`.

---

## If something goes wrong / cost hygiene

| Situation | Command |
|-----------|---------|
| Did a run leave anything alive? | `scripts/guardrails.sh orphans` |
| Destroy everything the labs made | `scripts/guardrails.sh nuke` |
| A specific lab's destroy failed | `cd labs/<lab> && terraform destroy -auto-approve` |
| Find resources from one run by tag | `aws resourcegroupstaggingapi get-resources --tag-filters Key=run_id,Values=<run_id>` |

**Golden rules**
- Don't use `--keep` unless you mean to leave it running (you pay per hour).
- After any `--keep` session, destroy when done.
- Glance at the **Billing → Cost Explorer** console once after your first few runs
  to confirm everything really is near $0.
