#!/usr/bin/env bash
# Shared helpers for the aws-devops-labs runner.
# Sourced by run-lab.sh and friends. Not meant to be executed directly.

set -euo pipefail

# --- pretty logging -----------------------------------------------------------
_c_reset=$'\033[0m'; _c_blue=$'\033[34m'; _c_green=$'\033[32m'
_c_yellow=$'\033[33m'; _c_red=$'\033[31m'; _c_dim=$'\033[2m'

log()   { printf '%s[ lab ]%s %s\n'  "$_c_blue"   "$_c_reset" "$*"; }
ok()    { printf '%s[  ok ]%s %s\n'  "$_c_green"  "$_c_reset" "$*"; }
warn()  { printf '%s[warn ]%s %s\n'  "$_c_yellow" "$_c_reset" "$*" >&2; }
err()   { printf '%s[error]%s %s\n'  "$_c_red"    "$_c_reset" "$*" >&2; }
step()  { printf '\n%s==>%s %s\n' "$_c_green" "$_c_reset" "$*"; }

# --- preflight ----------------------------------------------------------------
# Verifies tooling + credentials before we touch the account.
preflight() {
  local missing=0
  for bin in terraform aws jq; do
    if ! command -v "$bin" >/dev/null 2>&1; then
      err "missing required tool: $bin  (run scripts/bootstrap.sh)"
      missing=1
    fi
  done
  [ "$missing" -eq 0 ] || exit 1

  if ! aws sts get-caller-identity >/dev/null 2>&1; then
    err "AWS credentials are not configured or are invalid."
    err "Run:  aws configure   (see docs/SETUP.md for a safe walkthrough)"
    exit 1
  fi

  : "${AWS_REGION:=${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
  if [ -z "${AWS_REGION:-}" ]; then
    err "No AWS region set. Export AWS_REGION=us-east-1 or run 'aws configure'."
    exit 1
  fi
  export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
  CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text)"
  ok "account=${ACCOUNT_ID}  region=${AWS_REGION}"
  log "caller: ${CALLER_ARN}"
}

# --- run identity -------------------------------------------------------------
# A short, deterministic-ish run id so resources are uniquely named and easy to
# find/clean up if a destroy ever fails. No Math.random here — use time + pid.
new_run_id() {
  printf 'r%s' "$(date +%Y%m%d-%H%M%S)-$$"
}
