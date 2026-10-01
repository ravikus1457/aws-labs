# ---------------------------------------------------------------------------
# Lab 06 bootstrap stack — the handful of FREE, long-lived resources that must
# exist BEFORE GitHub Actions can do anything:
#
#   1. S3 bucket      -> remote state for the app stack (versioned, encrypted,
#                        S3-native locking; no DynamoDB table needed on TF >= 1.10)
#   2. ECR repository -> CI pushes the image here BEFORE the app stack is applied
#   3. OIDC provider  -> AWS trusts GitHub's token issuer
#   4. Deploy role    -> what the workflow assumes; trust is pinned to ONE repo,
#                        ONE branch and ONE environment. No access keys anywhere.
#   5. AWS Budget     -> $5/month email alarm, lives here so it survives the
#                        nightly destroy of the app stack
#
# Applied ONCE, locally, by a human with the policy in ../docs/iam-bootstrap-policy.json.
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name       = "${var.project}-lab06"
  account_id = data.aws_caller_identity.current.account_id
  bucket     = "${local.name}-tfstate-${local.account_id}" # bucket names are global; account id makes it unique
  repo_name  = "${local.name}/app"
  oidc_host  = "token.actions.githubusercontent.com"
  oidc_arn   = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

# ---------------------------------------------------------------------------
# 1. Remote state bucket
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "tfstate" {
  bucket        = local.bucket
  force_destroy = true # lab: let `terraform destroy` remove it even with state versions inside
  tags          = { Name = local.bucket }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled" # every state write is recoverable
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# 2. ECR repository — scan on push, IMMUTABLE tags (an image tag is the git SHA
#    and can never be silently replaced), lifecycle policy so storage stays ~$0.
# ---------------------------------------------------------------------------
resource "aws_ecr_repository" "app" {
  name                 = local.repo_name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true # lab: destroy even if images exist

  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
  tags = { Name = local.repo_name }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged layers after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep only the newest ${var.ecr_keep_images} images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.ecr_keep_images
        }
        action = { type = "expire" }
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# 3. GitHub OIDC identity provider (one per account, hence the toggle)
# ---------------------------------------------------------------------------
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_oidc_provider ? 1 : 0
  url            = "https://${local.oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
  # AWS has validated GitHub's issuer against its own trusted CA list since
  # mid-2023, so the thumbprint is no longer load-bearing — but the API still
  # requires a value. These are GitHub's published root thumbprints.
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
  tags = { Name = "github-actions-oidc" }
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 0 : 1
  url   = "https://${local.oidc_host}"
}

# ---------------------------------------------------------------------------
# 4. Deploy role — trust policy is the security boundary
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "deploy_trust" {
  statement {
    sid     = "GitHubActionsOIDC"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
    # 'sub' is the claim that pins the role to this repo. A job that runs inside
    # a GitHub *environment* presents `repo:<owner/repo>:environment:<name>`
    # instead of the branch form, so both shapes are listed. Nothing else
    # (forks, PRs, other branches, other repos) matches.
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values = [
        "repo:${var.github_repo}:ref:refs/heads/${var.github_branch}",
        "repo:${var.github_repo}:environment:${var.github_environment}",
      ]
    }
  }

  dynamic "statement" {
    for_each = length(var.extra_trusted_principal_arns) > 0 ? [1] : []
    content {
      sid     = "HumanOperators"
      actions = ["sts:AssumeRole"]
      principals {
        type        = "AWS"
        identifiers = var.extra_trusted_principal_arns
      }
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "${local.name}-github-deploy"
  description          = "Assumed by GitHub Actions (OIDC) to build/push the Lab 06 image and apply/destroy the app stack"
  assume_role_policy   = data.aws_iam_policy_document.deploy_trust.json
  max_session_duration = 3600
  tags                 = { Name = "${local.name}-github-deploy" }
}

# What the role may do. Honest scoping:
#   - state bucket, ECR repo, IAM roles, PassRole -> resource-scoped (tight)
#   - VPC/ECS/ALB/Logs/CloudWatch/SNS            -> service-wide in ONE region.
#     Terraform needs Describe*/Create*/Delete* across dozens of ec2/elbv2
#     actions whose resource ARNs are unknowable before apply; the practical
#     boundary here is the region condition + the trust policy above.
data "aws_iam_policy_document" "deploy_permissions" {
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket", "s3:GetBucketVersioning"]
    resources = [aws_s3_bucket.tfstate.arn]
  }
  statement {
    sid       = "StateObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/lab06/*"]
  }

  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "EcrPushPull"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:DescribeRepositories",
      "ecr:GetDownloadUrlForLayer",
      "ecr:GetLifecyclePolicy",
      "ecr:GetRepositoryPolicy",
      "ecr:InitiateLayerUpload",
      "ecr:ListImages",
      "ecr:ListTagsForResource",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.app.arn]
  }

  statement {
    sid = "TaskRoles"
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
    resources = ["arn:${data.aws_partition.current.partition}:iam::${local.account_id}:role/${local.name}-*"]
  }
  statement {
    sid       = "PassTaskRolesToEcsOnly"
    actions   = ["iam:PassRole"]
    resources = ["arn:${data.aws_partition.current.partition}:iam::${local.account_id}:role/${local.name}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
  statement {
    sid       = "ServiceLinkedRoles"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["arn:${data.aws_partition.current.partition}:iam::${local.account_id}:role/aws-service-role/*"]
    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["ecs.amazonaws.com", "elasticloadbalancing.amazonaws.com"]
    }
  }

  statement {
    sid = "RegionalInfra"
    actions = [
      "ec2:*",
      "ecs:*",
      "elasticloadbalancing:*",
      "logs:*",
      "cloudwatch:*",
      "sns:*",
      "tag:GetResources",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "${local.name}-github-deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy_permissions.json
}

# ---------------------------------------------------------------------------
# 5. Budget — account-wide (a tag-filtered budget needs cost-allocation tags
#    activated first, which is a billing-console step; account-wide is the one
#    that always fires). Budgets are free.
# ---------------------------------------------------------------------------
resource "aws_budgets_budget" "lab" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = var.budget_limit_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
