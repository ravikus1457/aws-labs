# Shared-by-every-lab variables (same names the runner exports as TF_VAR_*).
variable "aws_region" {
  description = "AWS region for the lab"
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project tag on every resource (cost tracking + orphan cleanup)"
  type        = string
  default     = "awslabs"
}

variable "run_id" {
  description = "Tag only. The bootstrap stack is long-lived, so this is a constant"
  type        = string
  default     = "bootstrap"
}

# Lab-specific
variable "github_repo" {
  description = "GitHub owner/repo allowed to assume the deploy role via OIDC"
  type        = string
  default     = "ravikus1457/aws-labs"
}

variable "github_branch" {
  description = "Branch whose workflow runs may assume the deploy role"
  type        = string
  default     = "main"
}

variable "github_environment" {
  description = "GitHub Actions environment name used by the apply job (its OIDC 'sub' differs from a plain branch run)"
  type        = string
  default     = "lab06-production"
}

variable "extra_github_environments" {
  description = "Further GitHub environments whose approval-gated apply jobs may assume the deploy role (lab 07 reuses this role; its apply job runs in lab07-production). Re-apply this stack after adding one: it is an in-place trust-policy update"
  type        = list(string)
  default     = ["lab07-production"]
}

variable "create_oidc_provider" {
  description = "An AWS account can hold only ONE OIDC provider for token.actions.githubusercontent.com. Set false to reuse an existing one"
  type        = bool
  default     = true
}

variable "extra_trusted_principal_arns" {
  description = "Optional IAM principal ARNs (e.g. your bootstrap user) that may also assume the deploy role, so a human can run plan/apply locally through the SAME role CI uses"
  type        = list(string)
  default     = []
}

variable "alert_email" {
  description = "Email that receives the AWS Budgets notifications (no default: a budget without a subscriber is theater)"
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must look like an email address."
  }
}

variable "budget_limit_usd" {
  description = "Monthly cost budget (USD) for the whole account; notifies at 80% and 100% actual, 100% forecast"
  type        = string
  default     = "5"
}

variable "ecr_keep_images" {
  description = "ECR lifecycle: how many tagged images to keep (older ones are expired)"
  type        = number
  default     = 10
}
