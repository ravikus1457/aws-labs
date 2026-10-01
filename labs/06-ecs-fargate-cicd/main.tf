# ---------------------------------------------------------------------------
# Lab 06 app stack — ECS Fargate service in private subnets behind a public ALB,
# deployed by GitHub Actions through an OIDC role. Split by concern:
#
#   network.tf     VPC, 2 public + 2 private subnets, IGW, 1 NAT, route tables
#   alb.tf         ALB, target group (/healthz), listeners, security groups
#   iam.tf         task-execution role (least privilege) + task role (empty)
#   ecs.tf         cluster, log group, task definition, service (2 tasks)
#   monitoring.tf  SNS topic + 5xx-rate and unhealthy-host alarms
#   outputs.tf     what exercise.sh and the CI smoke test read
#
# Long-lived resources it depends on (ECR repo, OIDC role, state bucket, budget)
# live in ./bootstrap and are applied once by a human. See README "Bootstrap order".
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

# Created by ../bootstrap; CI pushes <repo_url>:<git-sha> before this stack is applied.
data "aws_ecr_repository" "app" {
  name = var.ecr_repository_name
}

locals {
  # Stable name (no run_id) because CI re-applies this stack in place; run_id is a tag.
  name      = "${var.project}-lab06"
  azs       = slice(data.aws_availability_zones.available.names, 0, 2)
  image_uri = "${data.aws_ecr_repository.app.repository_url}:${var.image_tag}"
}
