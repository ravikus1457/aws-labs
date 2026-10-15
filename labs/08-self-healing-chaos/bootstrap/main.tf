# ---------------------------------------------------------------------------
# Lab 08 bootstrap — ONE free resource: an extra inline policy on lab 06's
# GitHub deploy role so the same role can also build, break and destroy lab 08.
# Same pattern as labs/07-eks-observability/bootstrap.
#
# What lab 08 needs beyond lab 06's policy: Auto Scaling, CloudWatch Synthetics
# (and the Lambda function/layer Synthetics creates for the canary), FIS,
# EventBridge rules, an SSM Automation document, five awslabs-lab08-* IAM roles
# plus an instance profile, the canary artifact bucket, and its own state key.
# Scoped to awslabs-lab08-* / cwsyn-* names wherever the service allows it.
#
# The role's TRUST policy (which GitHub jobs may assume it) lives in lab 06's
# bootstrap: its extra_github_environments must include "lab08-production"
# (README "Bootstrap order", step 1).
#
# Applied ONCE, locally, by a human; the lab 06 bootstrap user's policy
# (iam:PutRolePolicy on awslabs-lab06-*) is enough. IAM caps the INLINE policies
# of one role at 10,240 characters in total (lab 06 + lab 07 + this one); the
# README records the measured sizes.
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name       = "${var.project}-lab08"
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  arn_prefix = "arn:${local.partition}"
  # arn:aws:iam::123456789012:role/awslabs-lab06-github-deploy -> awslabs-lab06-github-deploy
  deploy_role_name = element(split("/", var.deploy_role_arn), length(split("/", var.deploy_role_arn)) - 1)
  state_bucket_arn = "${local.arn_prefix}:s3:::${var.tf_state_bucket}"
}

data "aws_iam_role" "deploy" {
  name = local.deploy_role_name
}

data "aws_iam_policy_document" "lab08" {
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket", "s3:GetBucketVersioning"]
    resources = [local.state_bucket_arn]
  }
  statement {
    sid       = "StateObjectsLab08Only"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.state_bucket_arn}/lab08/*"]
  }
  # The canary's artifact bucket (awslabs-lab08-canary-<account>), created + destroyed by the app stack.
  statement {
    sid       = "CanaryBucket"
    actions   = ["s3:*"]
    resources = ["${local.arn_prefix}:s3:::${local.name}-canary-*"]
  }

  # Services whose resource ARNs are unknowable before apply (or that Terraform
  # must Describe across the region): region-scoped, like lab 06's policy.
  statement {
    sid = "RegionalInfra"
    actions = [
      "autoscaling:*",
      "cloudwatch:*",
      "ec2:*",
      "elasticloadbalancing:*",
      "fis:*",
      "logs:*",
      "synthetics:*",
      "tag:GetResources",
      "lambda:ListFunctions",
      "lambda:ListLayers",
      "lambda:GetLayerVersion",
      "ssm:Describe*",
      "ssm:List*",
      "ssm:GetDocument",
      "ssm:GetAutomationExecution",
      "ssm:GetCommandInvocation",
      "events:ListRules",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }
  # EventBridge rules + the runbook document: lab 08 names only.
  statement {
    sid     = "Lab08RulesAndRunbook"
    actions = ["events:*", "ssm:CreateDocument", "ssm:UpdateDocument", "ssm:DeleteDocument", "ssm:UpdateDocumentDefaultVersion", "ssm:AddTagsToResource", "ssm:RemoveTagsFromResource"]
    resources = [
      "${local.arn_prefix}:events:${var.aws_region}:${local.account_id}:rule/${local.name}-*",
      "${local.arn_prefix}:ssm:${var.aws_region}:${local.account_id}:document/${local.name}-*",
    ]
  }
  # exercise.sh checks stress-ng/tc/the interface on the instances before any chaos
  # runs: AWS-RunShellScript, and only on instances tagged lab08-role=web.
  statement {
    sid       = "PreflightCommandDocument"
    actions   = ["ssm:SendCommand"]
    resources = ["${local.arn_prefix}:ssm:${var.aws_region}::document/AWS-RunShellScript"]
  }
  statement {
    sid       = "PreflightCommandLab08InstancesOnly"
    actions   = ["ssm:SendCommand"]
    resources = ["${local.arn_prefix}:ec2:${var.aws_region}:${local.account_id}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/lab08-role"
      values   = ["web"]
    }
  }
  # Synthetics creates (and, with delete_lambda, deletes) a Lambda function and a
  # layer named cwsyn-<canary>-<uuid> with the CALLER's permissions.
  statement {
    sid     = "SyntheticsLambda"
    actions = ["lambda:*"]
    resources = [
      "${local.arn_prefix}:lambda:${var.aws_region}:${local.account_id}:function:cwsyn-*",
      "${local.arn_prefix}:lambda:${var.aws_region}:${local.account_id}:layer:cwsyn-*",
    ]
  }

  # CreateCanary builds the canary's Lambda layer with the CALLER's credentials from AWS's
  # own Synthetics library bucket (first real run 10-15: AccessDenied GetObjectVersion on
  # aws-synthetics-library-us-east-1/v1-0/pythonSeleniumLibrary.zip).
  statement {
    sid       = "SyntheticsLibraryRead"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["arn:aws:s3:::aws-synthetics-library-*/*"]
  }

  # The five roles + one instance profile this lab creates.
  statement {
    sid = "Lab08RolesAndProfile"
    actions = [
      "iam:AddRoleToInstanceProfile",
      "iam:AttachRolePolicy",
      "iam:CreateInstanceProfile",
      "iam:CreateRole",
      "iam:DeleteInstanceProfile",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:GetInstanceProfile",
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:ListRolePolicies",
      "iam:ListRoleTags",
      "iam:PutRolePolicy",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:TagInstanceProfile",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
      "iam:UpdateRoleDescription",
    ]
    resources = [
      "${local.arn_prefix}:iam::${local.account_id}:role/${local.name}-*",
      "${local.arn_prefix}:iam::${local.account_id}:instance-profile/${local.name}-*",
    ]
  }
  # Each lab 08 role may be handed only to the service it was built for.
  statement {
    sid       = "PassLab08Roles"
    actions   = ["iam:PassRole"]
    resources = ["${local.arn_prefix}:iam::${local.account_id}:role/${local.name}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ec2.amazonaws.com", "lambda.amazonaws.com", "synthetics.amazonaws.com", "fis.amazonaws.com", "events.amazonaws.com"]
    }
  }
  statement {
    sid       = "ReadManagedPolicies"
    actions   = ["iam:GetPolicy", "iam:GetPolicyVersion"]
    resources = ["${local.arn_prefix}:iam::aws:policy/*"]
  }
  # FIS creates AWSServiceRoleForFIS on first use; the ASG/ELB ones usually exist already.
  statement {
    sid       = "ServiceLinkedRoles"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["${local.arn_prefix}:iam::${local.account_id}:role/aws-service-role/*"]
    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["fis.amazonaws.com", "autoscaling.amazonaws.com", "elasticloadbalancing.amazonaws.com"]
    }
  }
  statement {
    sid       = "ListForSurvivorCheck"
    actions   = ["iam:ListRoles", "iam:ListInstanceProfiles", "s3:ListAllMyBuckets"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "lab08" {
  name   = "${local.name}-github-deploy"
  role   = data.aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.lab08.json
}
