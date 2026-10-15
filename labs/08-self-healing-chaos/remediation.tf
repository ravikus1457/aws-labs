# ---------------------------------------------------------------------------
# Event-driven remediation: alarm state change -> EventBridge -> SSM Automation.
#
#   cpu-high  OK    -> ALARM   rule "scale-out"  runbook Direction=out (+1, capped at max)
#   cpu-high  ALARM -> OK      rule "scale-in"   runbook Direction=in  (back to baseline)
#
# No Lambda, no polling: CloudWatch publishes every alarm state change to the
# default event bus for free. The runbook is a versioned SSM document, so the
# remediation itself is code, reviewed in a PR, with an execution history.
#
# Why not a target-tracking scaling policy? It would do the scale-out alone, and
# in production it is often the right tool. This lab wires the alarm -> runbook
# path by hand because that is the pattern for remediations a scaling policy
# cannot express (restart a service, rotate a node, fail over), and because it
# leaves an execution record per incident that chaos.sh can time.
# ---------------------------------------------------------------------------

resource "aws_ssm_document" "scale" {
  name            = "${local.name}-scale"
  document_type   = "Automation"
  document_format = "YAML"
  content         = file("${path.module}/runbooks/scale.yaml")
}

resource "aws_cloudwatch_event_rule" "cpu_alarm" {
  name        = "${local.name}-cpu-alarm"
  description = "cpu-high entered ALARM -> scale out by one instance"
  event_pattern = jsonencode({
    source        = ["aws.cloudwatch"]
    "detail-type" = ["CloudWatch Alarm State Change"]
    resources     = [aws_cloudwatch_metric_alarm.cpu_high.arn]
    detail = {
      state = { value = ["ALARM"] }
    }
  })
}

resource "aws_cloudwatch_event_rule" "cpu_ok" {
  name        = "${local.name}-cpu-ok"
  description = "cpu-high returned from ALARM to OK -> scale back in to the baseline"
  event_pattern = jsonencode({
    source        = ["aws.cloudwatch"]
    "detail-type" = ["CloudWatch Alarm State Change"]
    resources     = [aws_cloudwatch_metric_alarm.cpu_high.arn]
    detail = {
      state         = { value = ["OK"] }
      previousState = { value = ["ALARM"] } # INSUFFICIENT_DATA -> OK at creation is not a recovery
    }
  })
}

locals {
  runbook_target_arn = "arn:${local.partition}:ssm:${var.aws_region}:${local.account_id}:automation-definition/${aws_ssm_document.scale.name}:$DEFAULT"
  runbook_common = {
    AutomationAssumeRole = [aws_iam_role.remediation.arn]
    AutoScalingGroupName = [aws_autoscaling_group.web.name]
    BaselineCapacity     = [tostring(var.baseline_capacity)]
    MaxCapacity          = [tostring(var.max_capacity)]
  }
}

resource "aws_cloudwatch_event_target" "scale_out" {
  rule      = aws_cloudwatch_event_rule.cpu_alarm.name
  target_id = "scale-out-runbook"
  arn       = local.runbook_target_arn
  role_arn  = aws_iam_role.events.arn
  input     = jsonencode(merge(local.runbook_common, { Direction = ["out"] }))
  dead_letter_config {
    arn = aws_sqs_queue.remediation_dlq.arn
  }
}

resource "aws_cloudwatch_event_target" "scale_in" {
  rule      = aws_cloudwatch_event_rule.cpu_ok.name
  target_id = "scale-in-runbook"
  arn       = local.runbook_target_arn
  role_arn  = aws_iam_role.events.arn
  input     = jsonencode(merge(local.runbook_common, { Direction = ["in"] }))
  dead_letter_config {
    arn = aws_sqs_queue.remediation_dlq.arn
  }
}

# --- failed-invocation capture ------------------------------------------------
# The first real run (10-15) showed EventBridge MatchedEvents=1, FailedInvocations=1 and no
# way to see WHY. A target DLQ receives the event with ERROR_CODE / ERROR_MESSAGE message
# attributes, so a failed remediation states its own reason (exercise.sh prints it).
resource "aws_sqs_queue" "remediation_dlq" {
  name                      = "${local.name}-remediation-dlq"
  message_retention_seconds = 86400
  sqs_managed_sse_enabled   = true
}

data "aws_iam_policy_document" "remediation_dlq" {
  statement {
    sid       = "EventBridgeRulesOnly"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.remediation_dlq.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.cpu_alarm.arn, aws_cloudwatch_event_rule.cpu_ok.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "remediation_dlq" {
  queue_url = aws_sqs_queue.remediation_dlq.id
  policy    = data.aws_iam_policy_document.remediation_dlq.json
}
