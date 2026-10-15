# ---------------------------------------------------------------------------
# Lab 09 bootstrap — ONE free resource: an extra policy on lab 06's GitHub deploy
# role so the same role can also build, test and destroy this lab. Same pattern as
# labs/07-eks-observability/bootstrap (no second role, no second trust policy).
#
# Scope: every statement is limited to names starting awslabs-lab09- where the
# service has name-bearing ARNs (Lambda, SQS, Step Functions, DynamoDB, IAM roles,
# log groups), and to the lab region where it does not (API Gateway, CloudWatch
# metrics/alarms/dashboards, X-Ray reads).
#
# The role's TRUST policy (which GitHub jobs may assume it) lives in lab 06's
# bootstrap: its extra_github_environments must include "lab09-production" before
# the approval-gated jobs can assume the role. See README "Bootstrap order".
#
# Applied ONCE, locally, by a human.
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name       = "${var.project}-lab09"
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = var.aws_region
  # arn:aws:iam::123456789012:role/awslabs-lab06-github-deploy -> awslabs-lab06-github-deploy
  deploy_role_name = element(split("/", var.deploy_role_arn), length(split("/", var.deploy_role_arn)) - 1)
  state_bucket_arn = "arn:${local.partition}:s3:::${var.tf_state_bucket}"
  arn_prefix       = "arn:${local.partition}"
  acct_region      = "${local.region}:${local.account_id}"
}

data "aws_iam_role" "deploy" {
  name = local.deploy_role_name
}

data "aws_iam_policy_document" "lab09" {
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket", "s3:GetBucketVersioning"]
    resources = [local.state_bucket_arn]
  }
  statement {
    sid       = "StateObjectsLab09Only"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.state_bucket_arn}/lab09/*"]
  }

  statement {
    sid     = "Lab09NamedResources"
    actions = ["lambda:*", "sqs:*", "states:*", "dynamodb:*"]
    resources = [
      "${local.arn_prefix}:lambda:${local.acct_region}:function:${local.name}-*",
      "${local.arn_prefix}:lambda:${local.acct_region}:event-source-mapping:*",
      "${local.arn_prefix}:sqs:${local.acct_region}:${local.name}-*",
      "${local.arn_prefix}:states:${local.acct_region}:stateMachine:${local.name}-*",
      "${local.arn_prefix}:states:${local.acct_region}:execution:${local.name}-*",
      "${local.arn_prefix}:states:${local.acct_region}:express:${local.name}-*",
      "${local.arn_prefix}:dynamodb:${local.acct_region}:table/${local.name}-*",
    ]
  }

  statement {
    sid     = "Lab09LogGroups"
    actions = ["logs:*"]
    resources = [
      "${local.arn_prefix}:logs:${local.acct_region}:log-group:/aws/lambda/${local.name}-*",
      "${local.arn_prefix}:logs:${local.acct_region}:log-group:/aws/vendedlogs/states/${local.name}-*",
    ]
  }

  # Actions with no resource-level permissions (or whose resource is unknowable before
  # create): listing for the survivor check, the account concurrency read in the plan
  # job, event-source-mapping creation, Step Functions' vended-log delivery, and the
  # region-scoped services the smoke/load scripts read (API Gateway ids are random, so
  # API Gateway cannot be name-scoped).
  statement {
    sid = "RegionalNoResourceScope"
    actions = [
      "lambda:CreateEventSourceMapping",
      "lambda:ListEventSourceMappings",
      "lambda:GetAccountSettings",
      "lambda:ListFunctions",
      "sqs:ListQueues",
      "states:ListStateMachines",
      "states:ValidateStateMachineDefinition", # first real run 10-15: the provider validates before CreateStateMachine
      "dynamodb:ListTables",
      "logs:DescribeLogGroups",
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:GetQueryResults",
      "apigateway:*",
      "cloudwatch:*",
      "xray:GetTraceSummaries",
      "xray:BatchGetTraces",
      "xray:GetServiceGraph",
      "tag:GetResources",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  # The five roles this lab creates (four Lambdas + the state machine) + their inline policies.
  statement {
    sid = "Lab09Roles"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:ListRoleTags",
      "iam:PutRolePolicy",
      "iam:GetRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
    ]
    resources = ["${local.arn_prefix}:iam::${local.account_id}:role/${local.name}-*"]
  }
  # Only Lambda and Step Functions may be handed a lab 09 role.
  statement {
    sid       = "PassLab09RolesToLambdaAndStatesOnly"
    actions   = ["iam:PassRole"]
    resources = ["${local.arn_prefix}:iam::${local.account_id}:role/${local.name}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com", "states.amazonaws.com"]
    }
  }
  # API Gateway creates its service-linked role on the first stage with logging/tracing
  # (first real run 10-15: CreateStage 403 "Caller does not have permissions to create a
  # Service Linked Role"). Only that one service's role.
  statement {
    sid       = "ApiGatewayServiceLinkedRoleOnly"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["${local.arn_prefix}:iam::${local.account_id}:role/aws-service-role/ops.apigateway.amazonaws.com/*"]
    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["ops.apigateway.amazonaws.com"]
    }
  }
  statement {
    sid       = "ListRolesForSurvivorCheck"
    actions   = ["iam:ListRoles"]
    resources = ["*"]
  }
}

# Default: an INLINE policy, like lab 07 (the lab 06 bootstrap user may PutRolePolicy on
# awslabs-lab06-* roles). IAM caps the AGGREGATE size of a role's inline policies at
# 10,240 characters; with labs 06, 07, 08 and 09 all adding inline policies to one role
# that cap can be hit (PutRolePolicy -> LimitExceeded). Then set
# attach_as_managed_policy = true: a customer-managed policy (6,144 chars of its own,
# up to 10 attached per role) - which needs iam:CreatePolicy/AttachRolePolicy for
# whoever applies this stack.
resource "aws_iam_role_policy" "lab09" {
  count  = var.attach_as_managed_policy ? 0 : 1
  name   = "${local.name}-github-deploy"
  role   = data.aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.lab09.json
}

resource "aws_iam_policy" "lab09" {
  count       = var.attach_as_managed_policy ? 1 : 0
  name        = "${local.name}-github-deploy"
  description = "Lab 09 permissions for lab 06's GitHub deploy role"
  policy      = data.aws_iam_policy_document.lab09.json
}

resource "aws_iam_role_policy_attachment" "lab09" {
  count      = var.attach_as_managed_policy ? 1 : 0
  role       = data.aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.lab09[0].arn
}
