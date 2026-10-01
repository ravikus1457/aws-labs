# ---------------------------------------------------------------------------
# Lab 07 bootstrap — ONE free resource: an extra inline policy on lab 06's
# GitHub deploy role so the same role can also build and destroy this lab.
#
# Why not a new role: the OIDC provider, the state bucket, the budget and the
# role all already exist (labs/06-ecs-fargate-cicd/bootstrap). A second role is
# a second trust policy to audit for no gain. What lab 07 needs on top of
# lab 06's policy is EKS itself, IAM for the cluster/node/IRSA roles, the
# cluster's OIDC provider, and its own state key — all added here, all scoped
# to awslabs-lab07-* where the service lets us.
#
# The role's TRUST policy (which GitHub jobs may assume it) lives in lab 06's
# bootstrap and lists both environments; see README "Bootstrap order".
#
# Applied ONCE, locally, by a human; the lab 06 bootstrap user's policy
# (iam:PutRolePolicy on awslabs-lab06-*) is enough.
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name       = "${var.project}-lab07"
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  # arn:aws:iam::123456789012:role/awslabs-lab06-github-deploy -> awslabs-lab06-github-deploy
  deploy_role_name = element(split("/", var.deploy_role_arn), length(split("/", var.deploy_role_arn)) - 1)
  state_bucket_arn = "arn:${local.partition}:s3:::${var.tf_state_bucket}"
}

data "aws_iam_role" "deploy" {
  name = local.deploy_role_name
}

data "aws_iam_policy_document" "lab07" {
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket", "s3:GetBucketVersioning"]
    resources = [local.state_bucket_arn]
  }
  statement {
    sid       = "StateObjectsLab07Only"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.state_bucket_arn}/lab07/*"]
  }

  # EKS has no useful resource ARNs before the cluster exists; region-scoped.
  statement {
    sid       = "EksRegional"
    actions   = ["eks:*"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  # The three roles this lab creates (cluster, node, app IRSA) + their policies.
  statement {
    sid = "Lab07Roles"
    actions = [
      "iam:AttachRolePolicy",
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:ListRolePolicies",
      "iam:ListRoleTags",
      "iam:PutRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
      "iam:UpdateRoleDescription",
    ]
    resources = ["arn:${local.partition}:iam::${local.account_id}:role/${local.name}-*"]
  }
  # Only EKS (control plane) and EC2 (kubelet) may be handed a lab 07 role.
  statement {
    sid       = "PassLab07RolesToEksAndEc2Only"
    actions   = ["iam:PassRole"]
    resources = ["arn:${local.partition}:iam::${local.account_id}:role/${local.name}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["eks.amazonaws.com", "ec2.amazonaws.com"]
    }
  }
  # Attaching AWS-managed policies needs to read them.
  statement {
    sid       = "ReadManagedPolicies"
    actions   = ["iam:GetPolicy", "iam:GetPolicyVersion", "iam:ListPolicyVersions"]
    resources = ["arn:${local.partition}:iam::aws:policy/*"]
  }
  # The cluster's OIDC provider (IRSA). Its ARN embeds the cluster's issuer id.
  statement {
    sid = "ClusterOidcProvider"
    actions = [
      "iam:CreateOpenIDConnectProvider",
      "iam:DeleteOpenIDConnectProvider",
      "iam:GetOpenIDConnectProvider",
      "iam:TagOpenIDConnectProvider",
      "iam:UntagOpenIDConnectProvider",
      "iam:ListOpenIDConnectProviderTags",
      "iam:UpdateOpenIDConnectProviderThumbprint",
    ]
    resources = ["arn:${local.partition}:iam::${local.account_id}:oidc-provider/oidc.eks.${var.aws_region}.amazonaws.com/id/*"]
  }
  # The destroy workflow's survivor check lists roles/providers to prove none
  # named awslabs-lab07-* remain. List* actions take no resource ARN.
  statement {
    sid       = "ListForSurvivorCheck"
    actions   = ["iam:ListRoles", "iam:ListOpenIDConnectProviders"]
    resources = ["*"]
  }
  statement {
    sid       = "ServiceLinkedRoles"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["arn:${local.partition}:iam::${local.account_id}:role/aws-service-role/*"]
    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["eks.amazonaws.com", "eks-nodegroup.amazonaws.com", "elasticloadbalancing.amazonaws.com"]
    }
  }

  # CreateNodegroup first checks whether AWSServiceRoleForAmazonEKSNodegroup exists, which needs
  # iam:GetRole on the service-linked role path. It cannot share the statement above: the
  # iam:AWSServiceName condition is absent from a GetRole request, so StringEquals would deny it.
  # (First real run, 2026-10-01: "Failed to validate if SLR ... missing permissions for 'iam:GetRole'".)
  statement {
    sid       = "ReadServiceLinkedRoles"
    actions   = ["iam:GetRole"]
    resources = ["arn:${local.partition}:iam::${local.account_id}:role/aws-service-role/*"]
  }

  # ec2/elbv2/logs are already in lab 06's policy for this region; listed again
  # so this policy is complete on its own if lab 06's is ever tightened.
  # autoscaling: the managed node group owns an ASG that the destroy job
  # inspects when counting survivors.
  statement {
    sid = "RegionalInfra"
    actions = [
      "ec2:*",
      "elasticloadbalancing:*",
      "logs:*",
      "autoscaling:Describe*",
      "tag:GetResources",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }
  # Read lab 06's ECR repo (image tag discovery in the workflow + the data source).
  statement {
    sid       = "EcrReadLab06Repo"
    actions   = ["ecr:DescribeRepositories", "ecr:DescribeImages", "ecr:ListImages", "ecr:ListTagsForResource"]
    resources = ["arn:${local.partition}:ecr:${var.aws_region}:${local.account_id}:repository/${var.ecr_repository_name}"]
  }
}

resource "aws_iam_role_policy" "lab07" {
  name   = "${local.name}-github-deploy"
  role   = data.aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.lab07.json
}

# EKS creates this service-linked role on the first CreateNodegroup, but first it checks whether the
# role exists, and that check failed twice on 2026-10-01 ("Failed to validate if SLR ... missing
# permissions for 'iam:GetRole'") even with GetRole allowed on the service-role path. Owning the role
# here makes the first node group deterministic. It is account-wide, free, and import it if it exists:
#   terraform import aws_iam_service_linked_role.eks_nodegroup \
#     arn:aws:iam::<account>:role/aws-service-role/eks-nodegroup.amazonaws.com/AWSServiceRoleForAmazonEKSNodegroup
resource "aws_iam_service_linked_role" "eks_nodegroup" {
  aws_service_name = "eks-nodegroup.amazonaws.com"
}
