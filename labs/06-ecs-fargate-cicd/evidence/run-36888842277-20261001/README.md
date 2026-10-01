# Lab 06 evidence — 2026-10-01, GitHub Actions run 36888842277

First end-to-end run of the flagship pipeline on the real account: lint → validate → build + push to ECR via OIDC →
plan → manual approval (environment `lab06-production`) → apply → smoke. Smoke assertions (from `exercise.log`):

- PASS: ALB /healthz returned 200 {"status":"ok"}
- PASS: 2 healthy targets (desired 2)
- PASS: service runningCount == desiredCount (2)
- PASS: every running task runs the ECR digest for tag bf99413aefce409d270e01391c1cbac0c72f4a90 (sha256:486cd195…)
- PASS: /version reports the deployed tag

Files: `exercise.log`, `service.json`, `target-health.json`, `task-digests.txt`, `version.json`, `scan-findings.json`.
The stack was destroyed by `lab06-destroy` the same hour. Two earlier runs the same day failed before/after the apply for
tooling reasons (artifact upload drops dotfiles → provider lock missing; `tee` before `mkdir`) and are kept in the
Actions history; both fixes are in the workflow.
