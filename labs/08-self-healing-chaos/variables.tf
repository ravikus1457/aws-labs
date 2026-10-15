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
  description = "Tag only in this lab (names are stable so CI can re-apply in place). CI leaves the default"
  type        = string
  default     = "lab08"
}

# Network + compute -----------------------------------------------------------
variable "vpc_cidr" {
  description = "CIDR block for the lab VPC (10.80/16: never overlaps lab 06's 10.60/16 or lab 07's 10.70/16, so all three can be up at once)"
  type        = string
  default     = "10.80.0.0/16"
}

variable "instance_type" {
  description = "Web instance type. t3.micro: 2 vCPU burstable, x86_64 (the AMI filter is x86_64; t4g.micro would need the arm64 AMI)"
  type        = string
  default     = "t3.micro"
}

variable "baseline_capacity" {
  description = "Steady-state number of web instances (ASG min and desired). 2 = N+1 across two AZs: one can die without an outage"
  type        = number
  default     = 2

  validation {
    condition     = var.baseline_capacity >= 2
    error_message = "baseline_capacity must be >= 2; with one instance, terminating it IS the outage and the lab measures nothing useful."
  }
}

variable "max_capacity" {
  description = "ASG max. The scale-out runbook adds one instance per ALARM and never exceeds this, so a stuck alarm cannot run up the bill"
  type        = number
  default     = 3
}

variable "app_port" {
  description = "Port the tiny Python app listens on (instances only accept it from the ALB security group)"
  type        = number
  default     = 8080
}

# Detection -------------------------------------------------------------------
variable "cpu_alarm_threshold" {
  description = "CPU % (Maximum across the ASG, 1-minute detailed monitoring) that counts as 'saturated' and triggers the scale-out runbook"
  type        = number
  default     = 80
}

variable "latency_alarm_threshold_seconds" {
  description = "ALB TargetResponseTime p90 (seconds) above which the latency alarm fires. The idle app answers in single-digit ms"
  type        = number
  default     = 0.15
}

variable "canary_runtime_version" {
  description = <<-EOT
    CloudWatch Synthetics runtime for the Python canary. Runtimes are deprecated on AWS's schedule,
    so the CI plan job resolves the newest non-deprecated `syn-python-selenium-*` version from
    `aws synthetics describe-runtime-versions` and overrides this default. The canary script itself
    uses only urllib (no browser), so any Python Synthetics runtime works.
  EOT
  type        = string
  default     = "syn-python-selenium-5.1"

  validation {
    condition     = can(regex("^syn-python-selenium-[0-9]+\\.[0-9]+$", var.canary_runtime_version))
    error_message = "canary_runtime_version must be a Python Synthetics runtime (syn-python-selenium-N.N); the canary script is Python."
  }
}

# Faults ----------------------------------------------------------------------
variable "cpu_stress_seconds" {
  description = "How long AWSFIS-Run-CPU-Stress pins every web instance at 100% CPU. 180 s leaves time for 1-minute metrics + the alarm to fire while stress is still on. FIS bills per action-minute ($0.10), so this is ~$0.40 of a run"
  type        = number
  default     = 180
}

variable "latency_ms" {
  description = "Delay AWSFIS-Run-Network-Latency adds to every packet leaving the instance's interface"
  type        = number
  default     = 200
}

variable "latency_seconds" {
  description = "How long the network-latency fault lasts (the experiment is OPTIONAL: chaos.sh runs it only with CHAOS_LATENCY=1)"
  type        = number
  default     = 120
}

variable "network_interface" {
  description = "Interface name the latency fault shapes. AL2023 on Nitro names it ens5 (the FIS document's default, eth0, does not exist there); exercise.sh checks it over SSM before any chaos runs"
  type        = string
  default     = "ens5"
}
