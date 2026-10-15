# Orders queue + dead-letter queue.
#
# Visibility timeout = 6 x the worker timeout (AWS guidance for Lambda event sources):
# a message a worker is still processing must not reappear for another worker.
# A message whose workflow fails is reported in batchItemFailures, becomes visible
# again after the visibility timeout, and after max_receive_count deliveries SQS
# moves it to the DLQ. Both queues use SSE-SQS (AWS-managed keys, no KMS charge).

resource "aws_sqs_queue" "dlq" {
  name                      = "${local.name}-orders-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum: longer than the source queue's, so nothing ages out in the DLQ first
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "orders" {
  name                       = "${local.name}-orders"
  visibility_timeout_seconds = local.visibility_timeout_seconds
  message_retention_seconds  = 345600 # 4 days
  receive_wait_time_seconds  = 20     # long polling for any non-Lambda consumer (Lambda's pollers long-poll regardless)
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

# Only the orders queue may use this DLQ, and only it may be the target of a redrive
# (StartMessageMoveTask from the DLQ goes back to the queue named here).
resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.orders.arn]
  })
}
