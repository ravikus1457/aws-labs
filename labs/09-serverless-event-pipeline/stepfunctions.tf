# EXPRESS workflow, started synchronously by the worker (StartSyncExecution):
# Validate (Lambda) -> Enrich (Lambda) -> Persist (DynamoDB putItem, direct integration).
#
# Express, not Standard: Standard bills $25 per million STATE TRANSITIONS (3+ per
# order here) and keeps a 90-day history per execution; Express bills $1 per million
# executions + duration, runs up to 5 minutes, and is built for exactly this
# high-volume, short, idempotent shape. The trade-off is at-least-once semantics and
# no built-in execution history: history goes to CloudWatch Logs (level ERROR here,
# to keep log ingestion near zero) and X-Ray.

resource "aws_cloudwatch_log_group" "sfn" {
  # /aws/vendedlogs/ prefix: AWS's recommendation for vended-log destinations (keeps the
  # CloudWatch Logs resource policy small).
  name              = "/aws/vendedlogs/states/${local.sfn_name}"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${local.sfn_name}-role"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

data "aws_iam_policy_document" "sfn" {
  statement {
    sid       = "InvokeTheTwoTaskFunctions"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.leaf["validate"].arn, aws_lambda_function.leaf["enrich"].arn]
  }
  statement {
    sid       = "WriteOrderKeysOnly"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.orders.arn]
    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["ORDER#*"]
    }
  }
  statement {
    sid = "XRay"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
    ]
    resources = ["*"]
  }
  # Step Functions delivers logs through CloudWatch Logs "vended log" deliveries; these
  # APIs have no resource-level permissions (AWS documents this exact list).
  statement {
    sid = "LogDelivery"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "order-workflow"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

resource "aws_sfn_state_machine" "orders" {
  name     = local.sfn_name
  type     = "EXPRESS"
  role_arn = aws_iam_role.sfn.arn

  definition = templatefile("${path.module}/statemachine/order-workflow.asl.json", {
    validate_arn = aws_lambda_function.leaf["validate"].arn
    enrich_arn   = aws_lambda_function.leaf["enrich"].arn
    table_name   = aws_dynamodb_table.orders.name
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = false
    level                  = "ERROR"
  }

  tracing_configuration {
    enabled = true
  }

  depends_on = [aws_iam_role_policy.sfn] # CreateStateMachine checks the role can deliver logs
}
