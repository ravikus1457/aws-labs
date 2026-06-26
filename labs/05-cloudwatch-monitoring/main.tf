terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      project    = var.project
      run_id     = var.run_id
      lab        = "05-cloudwatch-monitoring"
      managed_by = "terraform"
    }
  }
}

locals {
  name = "${var.project}-${var.run_id}"
}

# ---------------------------------------------------------------------------
# SNS topic — where the alarm publishes when it fires. In production you'd
# subscribe email/Slack/PagerDuty; here we just prove the wiring exists.
# ---------------------------------------------------------------------------
resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
  tags = { Name = "${local.name}-alerts" }
}

# ---------------------------------------------------------------------------
# CloudWatch metric alarm watching a CUSTOM metric (AwsLabs/Demo/DemoLoad).
# Using a custom metric lets us drive the alarm with put-metric-data /
# set-alarm-state without standing up real (billable) infrastructure.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "cpu_high" {
  alarm_name          = "${local.name}-cpu-high"
  alarm_description   = "Fires when the custom DemoLoad metric exceeds the threshold"
  namespace           = "AwsLabs/Demo"
  metric_name         = "DemoLoad"
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.cpu_threshold
  treat_missing_data  = "notBreaching"

  dimensions = {
    Service = local.name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = { Name = "${local.name}-cpu-high" }
}

# ---------------------------------------------------------------------------
# CloudWatch dashboard — a single metric widget plotting the demo metric so
# you can eyeball the signal the alarm is evaluating.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${local.name}-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "DemoLoad (custom metric)"
          view   = "timeSeries"
          region = var.aws_region
          stat   = "Average"
          period = 60
          metrics = [
            ["AwsLabs/Demo", "DemoLoad", "Service", local.name]
          ]
          yAxis = {
            left = {
              min = 0
              max = 100
            }
          }
          annotations = {
            horizontal = [
              {
                label = "alarm threshold"
                value = var.cpu_threshold
              }
            ]
          }
        }
      }
    ]
  })
}
