# Four functions from one package, four roles, four policies. Each role can do the
# one job its function has and nothing else:
#
#   ingest    sqs:SendMessage on the orders queue
#   worker    receive/delete on the orders queue (event source mapping) + StartSyncExecution
#             on the one state machine
#   validate  nothing beyond logs + X-Ray (pure function)
#   enrich    dynamodb:BatchGetItem/GetItem on the table, ONLY for keys starting "SKU#"
#
# Every role also gets: write to ITS OWN log group (no logs:CreateLogGroup, so a
# late invocation during destroy cannot resurrect a deleted group), and X-Ray
# segment upload (active tracing).

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "fn" {
  for_each           = local.function_names
  name               = "${each.value}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.function_names
  name              = "/aws/lambda/${each.value}"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "fn_base" {
  for_each = local.function_names
  statement {
    sid       = "OwnLogGroupOnly"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.fn[each.key].arn}:*"]
  }
  statement {
    sid       = "XRayActiveTracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"] # X-Ray segment APIs have no resource-level permissions
  }
}

data "aws_iam_policy_document" "ingest" {
  source_policy_documents = [data.aws_iam_policy_document.fn_base["ingest"].json]
  statement {
    sid       = "SendToOrdersQueue"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.orders.arn]
  }
}

data "aws_iam_policy_document" "worker" {
  source_policy_documents = [data.aws_iam_policy_document.fn_base["worker"].json]
  statement {
    sid       = "ConsumeOrdersQueue" # what the event source mapping does on the function's behalf
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.orders.arn]
  }
  statement {
    sid       = "RunTheWorkflow"
    actions   = ["states:StartSyncExecution"]
    resources = [aws_sfn_state_machine.orders.arn]
  }
}

data "aws_iam_policy_document" "enrich" {
  source_policy_documents = [data.aws_iam_policy_document.fn_base["enrich"].json]
  statement {
    sid       = "ReadCatalogKeysOnly"
    actions   = ["dynamodb:BatchGetItem", "dynamodb:GetItem"]
    resources = [aws_dynamodb_table.orders.arn]
    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["SKU#*"]
    }
  }
}

# Separate resources (not for_each) on purpose: the worker's policy references the
# state machine, which references the validate/enrich functions; one for_each
# resource would make every function wait on the state machine (a cycle).
resource "aws_iam_role_policy" "ingest" {
  name   = "ingest"
  role   = aws_iam_role.fn["ingest"].id
  policy = data.aws_iam_policy_document.ingest.json
}

resource "aws_iam_role_policy" "worker" {
  name   = "worker"
  role   = aws_iam_role.fn["worker"].id
  policy = data.aws_iam_policy_document.worker.json
}

resource "aws_iam_role_policy" "validate" {
  name   = "validate"
  role   = aws_iam_role.fn["validate"].id
  policy = data.aws_iam_policy_document.fn_base["validate"].json
}

resource "aws_iam_role_policy" "enrich" {
  name   = "enrich"
  role   = aws_iam_role.fn["enrich"].id
  policy = data.aws_iam_policy_document.enrich.json
}

locals {
  # The three functions that do not depend on the state machine.
  leaf_functions = {
    ingest = {
      handler = "orders.ingest.handler"
      timeout = 5
      env     = { QUEUE_URL = aws_sqs_queue.orders.url }
    }
    validate = {
      handler = "orders.steps.validate"
      timeout = 5
      env     = {}
    }
    enrich = {
      handler = "orders.steps.enrich"
      timeout = 5
      env     = { TABLE_NAME = aws_dynamodb_table.orders.name }
    }
  }
}

resource "aws_lambda_function" "leaf" {
  for_each         = local.leaf_functions
  function_name    = local.function_names[each.key]
  role             = aws_iam_role.fn[each.key].arn
  runtime          = "python3.12"
  architectures    = [var.lambda_architecture]
  handler          = each.value.handler
  memory_size      = var.lambda_memory_mb
  timeout          = each.value.timeout
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  dynamic "environment" {
    for_each = length(each.value.env) > 0 ? [each.value.env] : []
    content {
      variables = environment.value
    }
  }

  tracing_config {
    mode = "Active"
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.fn[each.key].name
  }

  depends_on = [aws_cloudwatch_log_group.fn]
}

resource "aws_lambda_function" "worker" {
  function_name    = local.function_names.worker
  role             = aws_iam_role.fn["worker"].arn
  runtime          = "python3.12"
  architectures    = [var.lambda_architecture]
  handler          = "orders.worker.handler"
  memory_size      = var.lambda_memory_mb
  timeout          = var.worker_timeout_seconds
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = {
      STATE_MACHINE_ARN = aws_sfn_state_machine.orders.arn
      BATCH_PARALLELISM = tostring(var.worker_batch_parallelism)
    }
  }

  tracing_config {
    mode = "Active"
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.fn["worker"].name
  }

  depends_on = [aws_cloudwatch_log_group.fn]
}

# SQS -> worker. The mapping (not the function) polls the queue; ReportBatchItemFailures
# lets the worker fail single messages instead of the whole batch; maximum_concurrency
# caps how many workers run at once no matter how deep the queue gets.
resource "aws_lambda_event_source_mapping" "worker" {
  event_source_arn                   = aws_sqs_queue.orders.arn
  function_name                      = aws_lambda_function.worker.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 1
  function_response_types            = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = var.worker_max_concurrency
  }

  depends_on = [aws_iam_role_policy.worker] # the mapping validates the role's SQS permissions on create
}
