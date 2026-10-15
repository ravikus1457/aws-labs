# One dashboard, five alarms. The smoke test asserts the DLQ alarm actually goes to
# ALARM after it injects a poison message: an alarm that has never fired is untested.

locals {
  alarm_actions = var.alarm_topic_arn == "" ? [] : [var.alarm_topic_arn]
  api_dims      = ["ApiName", aws_api_gateway_rest_api.orders.name, "Stage", aws_api_gateway_stage.v1.stage_name]
  fn_order      = ["ingest", "worker", "validate", "enrich"]
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${local.name}-dlq-not-empty"
  alarm_description   = "A message failed max_receive_count times and is parked in the DLQ. Fix the cause, then: python tools/redrive.py --apply"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.dlq.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "queue_backlog_age" {
  alarm_name          = "${local.name}-queue-backlog-age"
  alarm_description   = "The oldest order has waited > 5 min: workers are not keeping up (concurrency cap, throttling, or a failing downstream)"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = aws_sqs_queue.orders.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 300
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# A RATE, not a count: 5 errors in 10 requests and 5 in 100,000 are different incidents.
resource "aws_cloudwatch_metric_alarm" "api_5xx_rate" {
  alarm_name          = "${local.name}-api-5xx-rate"
  alarm_description   = "More than 1% of POST /orders returned 5xx in a minute (ingest Lambda errors/throttles or SQS unavailable)"
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  metric_query {
    id          = "rate"
    expression  = "IF(requests > 0, 100 * errors / requests, 0)"
    label       = "5xx %"
    return_data = true
  }
  metric_query {
    id = "errors"
    metric {
      namespace   = "AWS/ApiGateway"
      metric_name = "5XXError"
      dimensions  = { ApiName = aws_api_gateway_rest_api.orders.name, Stage = aws_api_gateway_stage.v1.stage_name }
      stat        = "Sum"
      period      = 60
    }
  }
  metric_query {
    id = "requests"
    metric {
      namespace   = "AWS/ApiGateway"
      metric_name = "Count"
      dimensions  = { ApiName = aws_api_gateway_rest_api.orders.name, Stage = aws_api_gateway_stage.v1.stage_name }
      stat        = "Sum"
      period      = 60
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${local.name}-lambda-errors"
  alarm_description   = "Any of the four functions raised (note: the worker reports poison messages as batch item failures, not errors; Validate/Enrich errors DO count)"
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  metric_query {
    id          = "total"
    expression  = "SUM([${join(", ", [for k in local.fn_order : "e_${k}"])}])"
    label       = "errors, all functions"
    return_data = true
  }

  dynamic "metric_query" {
    for_each = local.fn_order
    content {
      id = "e_${metric_query.value}"
      metric {
        namespace   = "AWS/Lambda"
        metric_name = "Errors"
        dimensions  = { FunctionName = local.function_names[metric_query.value] }
        stat        = "Sum"
        period      = 60
      }
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "workflow_failed" {
  alarm_name          = "${local.name}-workflow-failed"
  alarm_description   = "Step Functions executions failed (each failed delivery of a poison message counts once)"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.orders.arn }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_dashboard" "pipeline" {
  dashboard_name = "${local.name}-pipeline"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 8, height = 6
        properties = {
          title  = "API requests and errors (per minute)"
          region = var.aws_region, stat = "Sum", period = 60, view = "timeSeries"
          metrics = [
            concat(["AWS/ApiGateway", "Count"], local.api_dims, [{ label = "requests" }]),
            concat(["AWS/ApiGateway", "4XXError"], local.api_dims, [{ label = "4xx" }]),
            concat(["AWS/ApiGateway", "5XXError"], local.api_dims, [{ label = "5xx" }]),
          ]
        }
      },
      {
        type = "metric", x = 8, y = 0, width = 8, height = 6
        properties = {
          title  = "Latency p50 / p99 (ms): API Gateway and ingest Lambda"
          region = var.aws_region, period = 60, view = "timeSeries"
          metrics = [
            concat(["AWS/ApiGateway", "Latency"], local.api_dims, [{ stat = "p50", label = "API p50" }]),
            concat(["AWS/ApiGateway", "Latency"], local.api_dims, [{ stat = "p99", label = "API p99" }]),
            ["AWS/Lambda", "Duration", "FunctionName", local.function_names.ingest, { stat = "p50", label = "ingest p50" }],
            ["AWS/Lambda", "Duration", "FunctionName", local.function_names.ingest, { stat = "p99", label = "ingest p99" }],
          ]
        }
      },
      {
        type = "metric", x = 16, y = 0, width = 8, height = 6
        properties = {
          title  = "Queue depth, oldest message age, DLQ"
          region = var.aws_region, stat = "Maximum", period = 60, view = "timeSeries"
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.orders.name, { label = "orders visible" }],
            ["AWS/SQS", "ApproximateNumberOfMessagesNotVisible", "QueueName", aws_sqs_queue.orders.name, { label = "orders in flight" }],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.dlq.name, { label = "DLQ", color = "#d62728" }],
            ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", aws_sqs_queue.orders.name, { label = "oldest age (s)", yAxis = "right" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 8, height = 6
        properties = {
          title  = "Lambda concurrent executions (max per minute)"
          region = var.aws_region, stat = "Maximum", period = 60, view = "timeSeries"
          metrics = concat(
            [for k in local.fn_order : ["AWS/Lambda", "ConcurrentExecutions", "FunctionName", local.function_names[k], { label = k }]],
            [["AWS/Lambda", "ConcurrentExecutions", { label = "account-wide" }]],
          )
        }
      },
      {
        type = "metric", x = 8, y = 6, width = 8, height = 6
        properties = {
          title  = "Errors and throttles (all stages)"
          region = var.aws_region, stat = "Sum", period = 60, view = "timeSeries"
          metrics = concat(
            [for k in local.fn_order : ["AWS/Lambda", "Errors", "FunctionName", local.function_names[k], { label = "${k} errors" }]],
            [for k in local.fn_order : ["AWS/Lambda", "Throttles", "FunctionName", local.function_names[k], { label = "${k} throttles" }]],
            [["AWS/States", "ExecutionsFailed", "StateMachineArn", aws_sfn_state_machine.orders.arn, { label = "workflow failed" }]],
          )
        }
      },
      {
        type = "metric", x = 16, y = 6, width = 8, height = 6
        properties = {
          title  = "Workflow throughput and DynamoDB writes"
          region = var.aws_region, stat = "Sum", period = 60, view = "timeSeries"
          metrics = [
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", aws_sfn_state_machine.orders.arn, { label = "workflow succeeded" }],
            ["AWS/States", "ExecutionTime", "StateMachineArn", aws_sfn_state_machine.orders.arn, { stat = "p99", label = "workflow p99 ms", yAxis = "right" }],
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", aws_dynamodb_table.orders.name, { label = "DynamoDB WRU" }],
          ]
        }
      },
      {
        type = "alarm", x = 0, y = 12, width = 24, height = 3
        properties = {
          title = "Alarms"
          alarms = [
            aws_cloudwatch_metric_alarm.dlq_not_empty.arn,
            aws_cloudwatch_metric_alarm.queue_backlog_age.arn,
            aws_cloudwatch_metric_alarm.api_5xx_rate.arn,
            aws_cloudwatch_metric_alarm.lambda_errors.arn,
            aws_cloudwatch_metric_alarm.workflow_failed.arn,
          ]
        }
      },
    ]
  })
}
