# ---------------------------------------------------------------------------
# Alerting: two alarms -> one SNS topic -> (optional) email.
#   5xx rate        : (target 5xx + ALB-generated 5xx) / requests > N% for 2 of
#                     3 minutes. ALB 5xx is included because a 503 with NO
#                     healthy targets never reaches a task and would otherwise
#                     be invisible to a target-only metric.
#   unhealthy hosts : any target failing /healthz for 2 minutes.
# Both treat "no data" as OK, so an idle lab (zero requests) stays green and
# the nightly destroy does not fire a page.
# ---------------------------------------------------------------------------
resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
  tags = { Name = "${local.name}-alerts" }
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx_rate" {
  alarm_name          = "${local.name}-5xx-rate"
  alarm_description   = "5xx responses exceed ${var.alarm_5xx_rate_percent}% of requests (ALB ${aws_lb.app.name})"
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.alarm_5xx_rate_percent
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  metric_query {
    id          = "rate"
    expression  = "IF(requests > 0, 100 * (target_5xx + elb_5xx) / requests, 0)"
    label       = "5xx rate (%)"
    return_data = true
  }
  metric_query {
    id = "requests"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "RequestCount"
      period      = 60
      stat        = "Sum"
      dimensions  = { LoadBalancer = aws_lb.app.arn_suffix }
    }
  }
  metric_query {
    id = "target_5xx"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_Target_5XX_Count"
      period      = 60
      stat        = "Sum"
      dimensions  = { LoadBalancer = aws_lb.app.arn_suffix }
    }
  }
  metric_query {
    id = "elb_5xx"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_ELB_5XX_Count"
      period      = 60
      stat        = "Sum"
      dimensions  = { LoadBalancer = aws_lb.app.arn_suffix }
    }
  }
  tags = { Name = "${local.name}-5xx-rate" }
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_hosts" {
  alarm_name          = "${local.name}-unhealthy-hosts"
  alarm_description   = "One or more targets failing /healthz (target group ${aws_lb_target_group.app.name})"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  dimensions = {
    LoadBalancer = aws_lb.app.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }
  tags = { Name = "${local.name}-unhealthy-hosts" }
}
