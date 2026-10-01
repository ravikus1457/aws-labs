# ---------------------------------------------------------------------------
# Lab 07 app stack — an EKS cluster (2 × t3.small managed nodes, IRSA, control-
# plane logs) that the workflow then fills with Helm: the lab 06 container
# behind an NLB and kube-prometheus-stack watching it. Split by concern:
#
#   network.tf   VPC, 2 public + 2 private subnets, IGW, 1 NAT, LB subnet tags
#   iam.tf       cluster role, node role, cluster OIDC provider, IRSA app role
#   eks.tf       log group, cluster, node group, metrics-server add-on,
#                access entries (cluster-admin for the CI role + named humans)
#   outputs.tf   what exercise.sh, the workflows and the README read
#
# Things this stack does NOT create, on purpose:
#   - the image: it comes from lab 06's ECR repo (looked up below)
#   - the deploy role / state bucket / budget: lab 06's bootstrap owns them;
#     ./bootstrap here only ADDS the EKS permissions that role needs
#   - the NLB: Kubernetes creates it from the chart's Service (type LoadBalancer),
#     so `helm uninstall` must run BEFORE `terraform destroy` (the destroy
#     workflow does, and force-deletes any NLB left behind before trying)
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

# Created by labs/06-ecs-fargate-cicd/bootstrap; lab 06's CI pushes <repo_url>:<git-sha>.
data "aws_ecr_repository" "app" {
  name = var.ecr_repository_name
}

locals {
  # Stable names (no run_id) because CI re-applies this stack in place; run_id is a tag.
  name         = "${var.project}-lab07"
  cluster_name = "${local.name}-eks"
  azs          = slice(data.aws_availability_zones.available.names, 0, 2)
  partition    = data.aws_partition.current.partition
  account_id   = data.aws_caller_identity.current.account_id

  # The principal that runs this apply becomes cluster-admin automatically
  # (bootstrap_cluster_creator_admin_permissions). EKS stores that access entry
  # under the IAM *role* ARN, while the caller identity of an assumed role is the
  # STS form (arn:aws:sts::<acct>:assumed-role/<role>/<session>). Normalise it so
  # we never try to create a second entry for the creator (ResourceInUse).
  caller_principal_arn = replace(
    data.aws_caller_identity.current.arn,
    "/^arn:([^:]+):sts::([0-9]+):assumed-role/([^/]+)/.*$/",
    "arn:$1:iam::$2:role/$3",
  )
  admin_principals = toset([
    for p in concat([var.deploy_role_arn], var.admin_principal_arns) :
    p if p != "" && p != local.caller_principal_arn
  ])
}
