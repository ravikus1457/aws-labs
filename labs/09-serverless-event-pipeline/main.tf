# ---------------------------------------------------------------------------
# Lab 09 app stack — an event-driven order pipeline, serverless end to end:
#
#   POST /orders -> API Gateway (REST, API key + usage plan, X-Ray)
#                -> ingest Lambda (shape checks, 202 in ms)
#                -> SQS orders queue (buffer; DLQ after max_receive_count)
#                -> worker Lambda (partial batch failures, capped concurrency)
#                -> Step Functions EXPRESS, started synchronously:
#                     Validate (Lambda) -> Enrich (Lambda, catalog read) -> Persist (DynamoDB direct)
#                -> DynamoDB table (on-demand; orders + catalog, single table)
#
# Split by concern:
#   api.tf            REST API, POST /orders, stage v1 (tracing), API key, usage plan
#   lambda.tf         package, 4 functions, 4 roles (one policy each), log groups, SQS mapping
#   queue.tf          orders queue + DLQ + redrive-allow policy
#   stepfunctions.tf  state machine (statemachine/order-workflow.asl.json), its role + logs
#   dynamodb.tf       table + catalog items
#   monitoring.tf     dashboard + 5 alarms
#   outputs.tf        what tools/smoke.py, tools/loadtest.py and the workflows read
#
# Nothing here bills by the hour except the dashboard ($3/month, prorated) and the
# alarms ($0.10/month each, prorated): idle cost is cents per day, and the 03:00 UTC
# destroy removes even that.
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

locals {
  # Stable names (no run_id) so CI can re-apply in place; run_id is a tag.
  name       = "${var.project}-lab09"
  account_id = data.aws_caller_identity.current.account_id

  function_names = {
    ingest   = "${local.name}-ingest"
    worker   = "${local.name}-worker"
    validate = "${local.name}-validate"
    enrich   = "${local.name}-enrich"
  }
  sfn_name                   = "${local.name}-order-workflow"
  visibility_timeout_seconds = 6 * var.worker_timeout_seconds
}

# One deployment package for all four functions (each uses a different handler).
# Built at plan time; the CI plan job ships build/lambda.zip with the saved plan so
# the apply job uploads exactly the bytes that were reviewed.
data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/src"
  output_path = "${path.module}/build/lambda.zip"
  excludes    = ["orders/__pycache__"]
}
