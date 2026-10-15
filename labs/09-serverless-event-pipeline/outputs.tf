# Read by tools/smoke.py, tools/loadtest.py, tools/redrive.py and the workflows
# (terraform output -json > outputs.json). Nothing secret here: the API key VALUE is
# fetched at run time from API Gateway (apigateway:GET), so outputs.json can be
# uploaded as a workflow artifact without leaking it.

output "region" {
  description = "Region the stack is in"
  value       = var.aws_region
}

output "name_prefix" {
  description = "Prefix of every named resource (alarms, functions, queues, roles)"
  value       = local.name
}

output "api_url" {
  description = "POST orders here (header x-api-key required)"
  value       = "${aws_api_gateway_stage.v1.invoke_url}/orders"
}

output "rest_api_id" {
  description = "REST API id"
  value       = aws_api_gateway_rest_api.orders.id
}

output "rest_api_name" {
  description = "REST API name (the ApiName dimension of its CloudWatch metrics)"
  value       = aws_api_gateway_rest_api.orders.name
}

output "stage_name" {
  description = "Deployed stage"
  value       = aws_api_gateway_stage.v1.stage_name
}

output "api_key_id" {
  description = "Id (not value) of the usage-plan key. Value: aws apigateway get-api-key --api-key <id> --include-value"
  value       = aws_api_gateway_api_key.loadtest.id
}

output "queue_url" {
  description = "Orders queue URL"
  value       = aws_sqs_queue.orders.url
}

output "queue_arn" {
  description = "Orders queue ARN (redrive destination)"
  value       = aws_sqs_queue.orders.arn
}

output "queue_name" {
  description = "Orders queue name (QueueName metric dimension)"
  value       = aws_sqs_queue.orders.name
}

output "dlq_url" {
  description = "Dead-letter queue URL"
  value       = aws_sqs_queue.dlq.url
}

output "dlq_arn" {
  description = "Dead-letter queue ARN (redrive source)"
  value       = aws_sqs_queue.dlq.arn
}

output "max_receive_count" {
  description = "Deliveries before a message moves to the DLQ"
  value       = var.max_receive_count
}

output "visibility_timeout_seconds" {
  description = "Orders queue visibility timeout"
  value       = local.visibility_timeout_seconds
}

output "table_name" {
  description = "DynamoDB table (orders + catalog)"
  value       = aws_dynamodb_table.orders.name
}

output "catalog" {
  description = "SKU -> price in cents, as written to the table"
  value       = { for k, v in var.catalog : k => v.price_cents }
}

output "state_machine_arn" {
  description = "Express state machine ARN"
  value       = aws_sfn_state_machine.orders.arn
}

output "function_names" {
  description = "Lambda function names by role"
  value       = local.function_names
}

output "function_memory_mb" {
  description = "Configured memory per function (the cost script multiplies Duration by it)"
  value = merge(
    { for k, f in aws_lambda_function.leaf : k => f.memory_size },
    { worker = aws_lambda_function.worker.memory_size },
  )
}

output "log_group_names" {
  description = "Every log group this stack owns (the cost script sums their IncomingBytes)"
  value       = concat([for g in aws_cloudwatch_log_group.fn : g.name], [aws_cloudwatch_log_group.sfn.name])
}

output "alarm_names" {
  description = "Alarm names by purpose"
  value = {
    dlq_not_empty     = aws_cloudwatch_metric_alarm.dlq_not_empty.alarm_name
    queue_backlog_age = aws_cloudwatch_metric_alarm.queue_backlog_age.alarm_name
    api_5xx_rate      = aws_cloudwatch_metric_alarm.api_5xx_rate.alarm_name
    lambda_errors     = aws_cloudwatch_metric_alarm.lambda_errors.alarm_name
    workflow_failed   = aws_cloudwatch_metric_alarm.workflow_failed.alarm_name
  }
}

output "dashboard_url" {
  description = "CloudWatch dashboard"
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.pipeline.dashboard_name}"
}

output "worker_settings" {
  description = "Back-pressure settings actually applied (the plan job may lower them for small accounts)"
  value = {
    max_concurrency   = var.worker_max_concurrency
    batch_parallelism = var.worker_batch_parallelism
    timeout_seconds   = var.worker_timeout_seconds
  }
}
