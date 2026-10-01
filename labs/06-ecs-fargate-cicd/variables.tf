# Variables shared by every lab (set by the runner via TF_VAR_*).
variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project tag applied to all resources (used for cost tracking + cleanup)"
  type        = string
  default     = "awslabs"
}

variable "run_id" {
  description = "Tag only in this lab (names are stable so CI can re-apply in place). The runner sets it per run; CI leaves the default"
  type        = string
  default     = "lab06"
}

# Lab-specific
variable "vpc_cidr" {
  description = "CIDR block for the lab VPC"
  type        = string
  default     = "10.60.0.0/16"
}

variable "ecr_repository_name" {
  description = "ECR repository created by ./bootstrap (looked up, not created, here)"
  type        = string
  default     = "awslabs-lab06/app"
}

variable "image_tag" {
  description = "Image tag to deploy. CI passes the git SHA it just pushed; tags are immutable so a tag is a build"
  type        = string
  default     = "latest"
}

variable "desired_count" {
  description = "Number of Fargate tasks (2 = one per AZ, survives a task or AZ loss)"
  type        = number
  default     = 2
}

variable "container_port" {
  description = "Port the app listens on inside the container"
  type        = number
  default     = 8080
}

variable "task_cpu" {
  description = "Fargate task CPU units (256 = 0.25 vCPU, the smallest)"
  type        = number
  default     = 256
}

variable "task_memory" {
  description = "Fargate task memory in MiB (512 is the floor for 256 CPU)"
  type        = number
  default     = 512
}

variable "log_retention_days" {
  description = "CloudWatch log retention (short = cheap + fully destroyable)"
  type        = number
  default     = 7
}

variable "enable_https" {
  description = "Add an HTTPS :443 listener and redirect :80 to it. Needs acm_certificate_arn (and therefore a domain). Off by default so the lab needs no DNS"
  type        = bool
  default     = false
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for the HTTPS listener (only read when enable_https = true)"
  type        = string
  default     = ""
}

variable "alert_email" {
  description = "Email subscribed to the alarm SNS topic. Empty = topic only (AWS emails a confirmation link; the subscription is inert until clicked)"
  type        = string
  default     = ""
}

variable "alarm_5xx_rate_percent" {
  description = "Alarm when (target 5xx + ALB 5xx) / requests exceeds this percentage"
  type        = number
  default     = 5
}
