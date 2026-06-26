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
  description = "Unique id for this run; makes resource names unique and easy to find"
  type        = string
  default     = "manual"
}
