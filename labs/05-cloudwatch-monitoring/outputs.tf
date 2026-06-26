output "sns_topic_arn" {
  description = "ARN of the SNS topic the alarm publishes to"
  value       = aws_sns_topic.alerts.arn
}

output "alarm_name" {
  description = "Name of the CloudWatch metric alarm"
  value       = aws_cloudwatch_metric_alarm.cpu_high.alarm_name
}

output "dashboard_name" {
  description = "Name of the CloudWatch dashboard"
  value       = aws_cloudwatch_dashboard.main.dashboard_name
}

output "service_dimension" {
  description = "Value of the Service dimension on the custom metric (for driving it in tests)"
  value       = local.name
}
