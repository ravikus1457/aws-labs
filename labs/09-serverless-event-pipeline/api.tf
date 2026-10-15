# REST API (not HTTP API) because X-Ray active tracing is a REST API stage feature;
# HTTP APIs ($1.00/M instead of $3.50/M) do not emit X-Ray segments. At this lab's
# volume the difference is under a cent per run; the trace is worth more.
#
# The API key is METERING, not authentication: it ties callers to a usage plan whose
# throttle and daily quota are a hard cap on what this public endpoint can cost.

resource "aws_api_gateway_rest_api" "orders" {
  name        = "${local.name}-orders-api"
  description = "Lab 09: POST /orders -> Lambda -> SQS -> Step Functions -> DynamoDB"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

resource "aws_api_gateway_resource" "orders" {
  rest_api_id = aws_api_gateway_rest_api.orders.id
  parent_id   = aws_api_gateway_rest_api.orders.root_resource_id
  path_part   = "orders"
}

resource "aws_api_gateway_method" "post_orders" {
  rest_api_id      = aws_api_gateway_rest_api.orders.id
  resource_id      = aws_api_gateway_resource.orders.id
  http_method      = "POST"
  authorization    = "NONE"
  api_key_required = true
}

resource "aws_api_gateway_integration" "post_orders" {
  rest_api_id             = aws_api_gateway_rest_api.orders.id
  resource_id             = aws_api_gateway_resource.orders.id
  http_method             = aws_api_gateway_method.post_orders.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST" # Lambda is always invoked with POST
  uri                     = aws_lambda_function.leaf["ingest"].invoke_arn
}

resource "aws_lambda_permission" "apigw_ingest" {
  statement_id  = "AllowApiGatewayPostOrders"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.leaf["ingest"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.orders.execution_arn}/*/POST/orders"
}

resource "aws_api_gateway_deployment" "orders" {
  rest_api_id = aws_api_gateway_rest_api.orders.id

  # A REST API deployment is a snapshot; redeploy whenever the routing changes.
  triggers = {
    redeploy = sha1(jsonencode([
      aws_api_gateway_resource.orders.id,
      aws_api_gateway_method.post_orders.id,
      aws_api_gateway_method.post_orders.api_key_required,
      aws_api_gateway_integration.post_orders.id,
      aws_api_gateway_integration.post_orders.uri,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [aws_api_gateway_integration.post_orders]
}

resource "aws_api_gateway_stage" "v1" {
  rest_api_id          = aws_api_gateway_rest_api.orders.id
  deployment_id        = aws_api_gateway_deployment.orders.id
  stage_name           = "v1"
  xray_tracing_enabled = true
  # Access/execution logging is deliberately off: it needs the ACCOUNT-wide API Gateway
  # CloudWatch role (aws_api_gateway_account), a global setting an ephemeral lab should
  # not own. Request counts, latency and 4xx/5xx come from the free stage metrics.
}

resource "aws_api_gateway_api_key" "loadtest" {
  name        = "${local.name}-loadtest"
  description = "Used by tools/smoke.py and tools/loadtest.py; value read at run time, never written to an artifact"
}

resource "aws_api_gateway_usage_plan" "orders" {
  name        = "${local.name}-orders"
  description = "Throttle + HARD daily quota: the cost ceiling of the public endpoint"

  api_stages {
    api_id = aws_api_gateway_rest_api.orders.id
    stage  = aws_api_gateway_stage.v1.stage_name
  }

  throttle_settings {
    rate_limit  = var.api_rate_limit
    burst_limit = var.api_burst_limit
  }

  quota_settings {
    limit  = var.api_daily_quota
    period = "DAY"
  }
}

resource "aws_api_gateway_usage_plan_key" "loadtest" {
  key_id        = aws_api_gateway_api_key.loadtest.id
  key_type      = "API_KEY"
  usage_plan_id = aws_api_gateway_usage_plan.orders.id
}
