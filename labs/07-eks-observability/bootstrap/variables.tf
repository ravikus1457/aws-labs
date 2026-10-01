# Shared-by-every-lab variables (same names the runner exports as TF_VAR_*).
variable "aws_region" {
  description = "AWS region the lab 07 app stack will be deployed into (the policy is scoped to it)"
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
variable "deploy_role_arn" {
  description = "ARN of lab 06's GitHub deploy role (labs/06-ecs-fargate-cicd/bootstrap: terraform output -raw deploy_role_arn). The lab 07 policy is attached to it"
  type        = string

  validation {
    condition     = can(regex("^arn:[^:]+:iam::[0-9]{12}:role/.+$", var.deploy_role_arn))
    error_message = "deploy_role_arn must be an IAM role ARN (arn:aws:iam::<account>:role/<name>)."
  }
}

variable "tf_state_bucket" {
  description = "Lab 06's state bucket name (terraform output -raw tfstate_bucket). Lab 07 keeps its state there under lab07/"
  type        = string
}

variable "ecr_repository_name" {
  description = "Lab 06's ECR repository the lab 07 workflow reads image tags from"
  type        = string
  default     = "awslabs-lab06/app"
}
