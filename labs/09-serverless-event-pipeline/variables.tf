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
  description = "Tag only in this lab (names are stable so CI can re-apply in place)"
  type        = string
  default     = "lab09"
}

# Lab-specific ---------------------------------------------------------------
variable "lambda_architecture" {
  description = "arm64 (Graviton) is ~20% cheaper per GB-second than x86_64 and the code is pure Python with no native dependencies, so nothing has to be cross-built"
  type        = string
  default     = "arm64"

  validation {
    condition     = contains(["arm64", "x86_64"], var.lambda_architecture)
    error_message = "lambda_architecture must be arm64 or x86_64."
  }
}

variable "lambda_memory_mb" {
  description = "Memory for every function. 128 MB is the minimum and enough for JSON in, one AWS call, JSON out; Lambda CPU scales with memory, so raise it if the load test shows Duration dominated by CPU"
  type        = number
  default     = 128
}

variable "worker_timeout_seconds" {
  description = "Worker Lambda timeout. One batch = up to 10 synchronous Express executions (~100-300 ms each warm). The queue's visibility timeout is derived from it (x6, AWS's guidance for SQS event sources)"
  type        = number
  default     = 15
}

variable "worker_batch_parallelism" {
  description = "Messages of one SQS batch processed in parallel threads inside the worker (each is an I/O-bound StartSyncExecution). Downstream Lambda concurrency is roughly worker_max_concurrency x this"
  type        = number
  default     = 4

  validation {
    condition     = var.worker_batch_parallelism >= 1 && var.worker_batch_parallelism <= 10
    error_message = "worker_batch_parallelism must be 1-10 (a batch holds at most 10 messages)."
  }
}

variable "worker_max_concurrency" {
  description = <<-EOT
    Ceiling on concurrent worker invocations (SQS event source scaling_config). This is the
    back-pressure knob: the API accepts bursts, the queue absorbs them, and the worker drains at a
    rate the downstream (Step Functions, DynamoDB) is allowed to see. Minimum 2. New AWS accounts can
    have an account-wide Lambda concurrency limit as low as 10; the CI plan job reads the limit and
    lowers this (and worker_batch_parallelism) when it is small.
  EOT
  type        = number
  default     = 5

  validation {
    condition     = var.worker_max_concurrency >= 2 && var.worker_max_concurrency <= 1000
    error_message = "worker_max_concurrency must be 2-1000 (the SQS event source minimum is 2)."
  }
}

variable "max_receive_count" {
  description = "Deliveries before SQS moves a message to the DLQ. 2 = one retry: Step Functions already retries transient Lambda/DynamoDB errors inside each attempt, so a message that fails twice is very likely poison, and the smoke test's fault path completes in ~3-4 min instead of ~10"
  type        = number
  default     = 2
}

variable "api_rate_limit" {
  description = "Usage-plan steady-state limit (requests/second) for the load-test API key"
  type        = number
  default     = 1000
}

variable "api_burst_limit" {
  description = "Usage-plan burst limit (token bucket size)"
  type        = number
  default     = 2000
}

variable "api_daily_quota" {
  description = "HARD daily request cap on the API key (usage-plan quota). It bounds what a leaked key or a runaway script can spend: 25,000 x $3.50/M = $0.09/day of API Gateway, with every downstream service bounded by the same count"
  type        = number
  default     = 25000
}

variable "log_retention_days" {
  description = "Retention for every log group this stack creates (they are Terraform-owned so destroy removes them)"
  type        = number
  default     = 7
}

variable "catalog" {
  description = "Product catalog written to DynamoDB as items (pk = SKU#<sku>). The smoke test's poison order uses a SKU that is deliberately NOT here, then adds it as the 'fix'"
  type = map(object({
    name        = string
    price_cents = number
  }))
  default = {
    "SKU-TSHIRT"  = { name = "Lab t-shirt", price_cents = 1999 }
    "SKU-MUG"     = { name = "Lab mug", price_cents = 1299 }
    "SKU-STICKER" = { name = "Sticker pack", price_cents = 299 }
    "SKU-HOODIE"  = { name = "Lab hoodie", price_cents = 4599 }
  }

  validation {
    condition     = alltrue([for k, v in var.catalog : can(regex("^SKU-[A-Z0-9][A-Z0-9-]{0,31}$", k)) && v.price_cents > 0])
    error_message = "catalog keys must match ^SKU-[A-Z0-9][A-Z0-9-]{0,31}$ (the same pattern the Lambdas enforce) and prices must be positive."
  }
}

variable "alarm_topic_arn" {
  description = "Optional SNS topic for alarm notifications (e.g. lab 06's alerts topic). Empty = alarms change state and show on the dashboard but notify nobody"
  type        = string
  default     = ""
}
