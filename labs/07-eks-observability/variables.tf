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
  default     = "lab07"
}

# Lab-specific ---------------------------------------------------------------
variable "vpc_cidr" {
  description = "CIDR block for the lab VPC (10.70/16: never overlaps lab 06's 10.60/16, so both can be up at once)"
  type        = string
  default     = "10.70.0.0/16"
}

variable "kubernetes_version" {
  description = <<-EOT
    EKS control-plane version. The AWS provider (5.100.0) passes this string through to the
    CreateCluster API without validating it, so "what the provider knows" is really "what EKS
    offers today". Default = 1.36: the newest version in STANDARD support on 2026-10-01
    (EKS release 2026-06-02, standard support until 2027-08-02). A version in EXTENDED support
    bills the control plane at $0.60/h instead of $0.10/h, so the CI plan job resolves the
    newest STANDARD_SUPPORT version from `aws eks describe-cluster-versions` and overrides this
    default; the cluster's upgrade_policy is STANDARD so it is never auto-enrolled in extended
    support. AL2 node AMIs stop at 1.32; the node group uses AL2023.
  EOT
  type        = string
  default     = "1.36"

  validation {
    condition     = can(regex("^1\\.[0-9]{2}$", var.kubernetes_version))
    error_message = "kubernetes_version must look like 1.NN (e.g. 1.36)."
  }
}

variable "node_instance_type" {
  description = "Managed node group instance type. t3.small (2 vCPU / 2 GiB, x86_64) because the lab 06 image is linux/amd64; t4g.small (arm64, ~20% cheaper) would need a multi-arch image build in lab 06"
  type        = string
  default     = "t3.small"
}

variable "node_ami_type" {
  description = "EKS-optimised AMI family. Must match the instance architecture (AL2023_x86_64_STANDARD for t3, AL2023_ARM_64_STANDARD for t4g). AL2 is not offered for Kubernetes >= 1.33"
  type        = string
  default     = "AL2023_x86_64_STANDARD"
}

variable "node_capacity_type" {
  description = "ON_DEMAND or SPOT. Spot is ~70% cheaper but a reclaim mid-smoke-test fails the run; the lab lives ~1 h, so on-demand is the honest default"
  type        = string
  default     = "ON_DEMAND"

  validation {
    condition     = contains(["ON_DEMAND", "SPOT"], var.node_capacity_type)
    error_message = "node_capacity_type must be ON_DEMAND or SPOT."
  }
}

variable "node_count" {
  description = "Desired (and minimum) nodes. 2 = one per AZ. No cluster-autoscaler is installed, so this is also effectively the maximum unless you change node_max"
  type        = number
  default     = 2
}

variable "node_max" {
  description = "Upper bound of the node group (manual headroom only; nothing scales nodes automatically)"
  type        = number
  default     = 3
}

variable "node_disk_gb" {
  description = "Root volume per node (gp3). 20 GB holds the AMI + a handful of images"
  type        = number
  default     = 20
}

variable "cluster_log_types" {
  description = <<-EOT
    EKS control-plane log types shipped to CloudWatch Logs. Default = the three low-volume ones.
    "api" and "audit" are OFF by default: audit alone writes several MB/hour on an idle cluster
    ($0.50/GB ingested) and is the first thing a cost review turns off in a lab. Set
    ["api","audit","authenticator","controllerManager","scheduler"] to see everything.
  EOT
  type        = list(string)
  default     = ["authenticator", "controllerManager", "scheduler"]

  validation {
    condition     = alltrue([for t in var.cluster_log_types : contains(["api", "audit", "authenticator", "controllerManager", "scheduler"], t)])
    error_message = "cluster_log_types may only contain api, audit, authenticator, controllerManager, scheduler."
  }
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the control-plane log group (short = cheap + fully destroyable). The group is created HERE so terraform destroy removes it; EKS-created groups never expire"
  type        = number
  default     = 7
}

variable "endpoint_public_access_cidrs" {
  description = "CIDRs allowed to reach the public Kubernetes API endpoint. GitHub-hosted runners have no fixed IPs, so the default is open; a real org uses a private endpoint + self-hosted runner in the VPC. Auth is still IAM (access entries), this is only the network door"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "deploy_role_arn" {
  description = <<-EOT
    ARN of the GitHub OIDC deploy role created by lab 06's bootstrap stack (its `deploy_role_arn`
    output / the AWS_DEPLOY_ROLE_ARN repo variable). This stack does not create a role: it grants
    the one it is given cluster-admin through an EKS access entry, so the workflow's kubectl/helm
    steps work even when a human created the cluster locally. Empty = no entry (the cluster
    creator still gets admin via bootstrap_cluster_creator_admin_permissions). The role's IAM
    permissions for EKS come from ./bootstrap (applied once by a human).
  EOT
  type        = string
  default     = ""
}

variable "admin_principal_arns" {
  description = "Extra IAM principal ARNs (users/roles) that get cluster-admin via access entries, e.g. your own user when you ran the apply through the deploy role but want kubectl as yourself"
  type        = list(string)
  default     = []
}

variable "app_namespace" {
  description = "Kubernetes namespace the lab06-app chart is installed into (the IRSA trust policy pins namespace + service account)"
  type        = string
  default     = "lab06"
}

variable "app_service_account" {
  description = "Service account name the chart creates (helm value serviceAccount.name); the IRSA role trusts exactly this one"
  type        = string
  default     = "lab06-app"
}

variable "ecr_repository_name" {
  description = "Lab 06's ECR repository (looked up, not created). The chart pulls the image from here and the IRSA demo policy is scoped to it"
  type        = string
  default     = "awslabs-lab06/app"
}

variable "metrics_server_addon" {
  description = "Install metrics-server as an EKS community add-on (what the HPA reads CPU from). false = install it yourself (e.g. the metrics-server Helm chart)"
  type        = bool
  default     = true
}

variable "oidc_thumbprint" {
  description = "Thumbprint recorded on the cluster's IAM OIDC provider. AWS validates oidc.eks.*.amazonaws.com against its own trusted CA list since 2023, so the value is no longer load-bearing, but the resource takes one; this is the Starfield Services Root CA G2 thumbprint every EKS IRSA guide uses"
  type        = string
  default     = "9e99a48a9960b14926bb7f3b02e22da2b0ab7280"
}
