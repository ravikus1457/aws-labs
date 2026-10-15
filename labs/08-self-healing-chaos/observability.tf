# ---------------------------------------------------------------------------
# Detection. Two independent views of "is it up":
#   - the Synthetics canary: outside-in, one real HTTP request per minute
#   - the ALB/EC2 metrics behind four alarms: inside-out, per signal
# chaos.sh additionally probes the ALB every 2 s, because a 1-per-minute canary
# cannot see an outage shorter than a minute (the README says which is which).
# ---------------------------------------------------------------------------

# --- canary artifact bucket (screenshots/HAR are not produced; logs + run reports are)
resource "aws_s3_bucket" "canary" {
  bucket        = "${local.name}-canary-${local.account_id}" # global namespace: account id makes it unique
  force_destroy = true                                       # lab: destroy even with run artifacts inside
  tags          = { Name = "${local.name}-canary" }
}

resource "aws_s3_bucket_public_access_block" "canary" {
  bucket                  = aws_s3_bucket.canary.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "canary" {
  bucket = aws_s3_bucket.canary.id
  rule {
    id     = "expire-canary-artifacts"
    status = "Enabled"
    filter {}
    expiration {
      days = 1
    }
  }
}

# --- the canary --------------------------------------------------------------
# Synthetics wants a zip with the script under python/. Built locally, no network.
data "archive_file" "canary" {
  type        = "zip"
  output_path = "${path.module}/.build/canary.zip"
  source {
    content  = file("${path.module}/canary/canary.py")
    filename = "python/canary.py"
  }
}

# aws_synthetics_canary has no source_code_hash argument; without this, editing canary.py
# would not redeploy the canary. A changed zip hash replaces it.
resource "terraform_data" "canary_code" {
  input = data.archive_file.canary.output_base64sha256
}

resource "aws_synthetics_canary" "alb" {
  name                 = "lab08-alb-probe" # <= 21 chars, lowercase; Synthetics creates Lambda cwsyn-lab08-alb-probe-<uuid>
  artifact_s3_location = "s3://${aws_s3_bucket.canary.bucket}/canary/"
  execution_role_arn   = aws_iam_role.canary.arn
  handler              = "canary.handler"
  zip_file             = data.archive_file.canary.output_path
  runtime_version      = var.canary_runtime_version
  start_canary         = true
  delete_lambda        = true # destroy removes the Lambda function + layer Synthetics created

  success_retention_period = 2
  failure_retention_period = 2

  schedule {
    expression = "rate(1 minute)" # the fastest Synthetics allows; $0.0012 per run
  }

  run_config {
    timeout_in_seconds = 30
    memory_in_mb       = 960 # the minimum Synthetics accepts
    environment_variables = {
      TARGET_URL      = "http://${aws_lb.web.dns_name}/healthz"
      TIMEOUT_SECONDS = "5"
    }
  }

  # The ALB answers only after targets are healthy; starting earlier would record
  # failed runs that say nothing about the experiments.
  depends_on = [aws_autoscaling_group.web, aws_lb_listener.http, aws_iam_role_policy.canary]

  lifecycle {
    replace_triggered_by = [terraform_data.canary_code]
  }
}

# --- alarms ------------------------------------------------------------------
# 1. Customer-facing: canary success < 50 % over two minutes. This is the FIS
#    STOP CONDITION: if an experiment actually takes the site down, FIS halts it.
#    Missing data (canary not started yet) is not an outage.
resource "aws_cloudwatch_metric_alarm" "canary" {
  alarm_name          = "${local.name}-canary-failing"
  alarm_description   = "Canary success < 50% for 2 consecutive minutes. FIS stop condition for every lab 08 experiment."
  namespace           = "CloudWatchSynthetics"
  metric_name         = "SuccessPercent"
  dimensions          = { CanaryName = aws_synthetics_canary.alb.name }
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 50
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
}

# 2. Capacity: fewer healthy targets than the baseline. Detects the
#    terminate-instance experiment. Remediation is the ASG itself (ELB health
#    checks); this alarm exists to TIME detection, not to trigger anything.
resource "aws_cloudwatch_metric_alarm" "healthy_hosts" {
  alarm_name          = "${local.name}-healthy-hosts-low"
  alarm_description   = "Healthy ALB targets below the baseline (${var.baseline_capacity}). The ASG replaces the instance; this alarm measures how fast we noticed."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  dimensions          = { TargetGroup = aws_lb_target_group.web.arn_suffix, LoadBalancer = aws_lb.web.arn_suffix }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 1
  threshold           = var.baseline_capacity
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
}

# 3. Saturation: the HOTTEST instance above the threshold. Maximum, not Average,
#    on purpose: with Average, the instance the runbook adds (idle) drags the mean
#    below the threshold, the alarm clears, the runbook scales back in, the mean
#    rises again -> flapping for as long as the stress lasts.
resource "aws_cloudwatch_metric_alarm" "cpu_high" {
  alarm_name          = "${local.name}-cpu-high"
  alarm_description   = "Max CPU across the ASG >= ${var.cpu_alarm_threshold}% for 1 minute. ALARM -> EventBridge -> runbook scales OUT; back to OK -> runbook scales IN to the baseline."
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  dimensions          = { AutoScalingGroupName = aws_autoscaling_group.web.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = var.cpu_alarm_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
}

# 4. Latency: ALB p90 target response time. Detect-only: when every instance is
#    slow, replacing or adding instances does not fix it, and pretending a runbook
#    does would be theatre. The lab measures time-to-detect for this one.
resource "aws_cloudwatch_metric_alarm" "latency_high" {
  alarm_name          = "${local.name}-latency-high"
  alarm_description   = "ALB p90 TargetResponseTime > ${var.latency_alarm_threshold_seconds}s for 1 minute. Detect-only (no automated fix for fleet-wide latency)."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  dimensions          = { LoadBalancer = aws_lb.web.arn_suffix }
  extended_statistic  = "p90"
  period              = 60
  evaluation_periods  = 1
  threshold           = var.latency_alarm_threshold_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching" # no requests = no datapoint, not slowness
}
