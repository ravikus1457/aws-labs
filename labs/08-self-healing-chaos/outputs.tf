output "aws_region" {
  description = "Region the stack is in"
  value       = var.aws_region
}

output "alb_url" {
  description = "Public URL of the app (HTTP)"
  value       = "http://${aws_lb.web.dns_name}"
}

output "alb_arn_suffix" {
  description = "ALB dimension value for AWS/ApplicationELB metrics"
  value       = aws_lb.web.arn_suffix
}

output "target_group_arn" {
  description = "Target group whose health chaos.sh samples every 5 s"
  value       = aws_lb_target_group.web.arn
}

output "asg_name" {
  description = "Auto Scaling group name"
  value       = aws_autoscaling_group.web.name
}

output "baseline_capacity" {
  description = "Steady-state instance count (ASG min/desired)"
  value       = var.baseline_capacity
}

output "max_capacity" {
  description = "ASG max (scale-out ceiling)"
  value       = var.max_capacity
}

output "canary_name" {
  description = "Synthetics canary name"
  value       = aws_synthetics_canary.alb.name
}

output "canary_runtime_version" {
  description = "Synthetics runtime the canary runs on"
  value       = aws_synthetics_canary.alb.runtime_version
}

output "alarm_names" {
  description = "The four alarms, by role"
  value = {
    canary        = aws_cloudwatch_metric_alarm.canary.alarm_name
    healthy_hosts = aws_cloudwatch_metric_alarm.healthy_hosts.alarm_name
    cpu_high      = aws_cloudwatch_metric_alarm.cpu_high.alarm_name
    latency_high  = aws_cloudwatch_metric_alarm.latency_high.alarm_name
  }
}

output "remediation_dlq_url" {
  description = "EventBridge target DLQ: a failed runbook start lands here with ERROR_CODE/ERROR_MESSAGE"
  value       = aws_sqs_queue.remediation_dlq.url
}

output "runbook_name" {
  description = "SSM Automation document EventBridge starts on cpu-high state changes"
  value       = aws_ssm_document.scale.name
}

output "event_rule_names" {
  description = "EventBridge rules wiring the cpu-high alarm to the runbook"
  value       = [aws_cloudwatch_event_rule.cpu_alarm.name, aws_cloudwatch_event_rule.cpu_ok.name]
}

output "fis_template_ids" {
  description = "FIS experiment template ids by experiment name (chaos.sh starts these)"
  value = {
    terminate       = aws_fis_experiment_template.terminate.id
    cpu_stress      = aws_fis_experiment_template.cpu_stress.id
    network_latency = aws_fis_experiment_template.network_latency.id
  }
}

output "fault_parameters" {
  description = "Fault sizes the templates were built with (copied into the evidence)"
  value = {
    cpu_stress_seconds = var.cpu_stress_seconds
    latency_ms         = var.latency_ms
    latency_seconds    = var.latency_seconds
    network_interface  = var.network_interface
  }
}

output "canary_bucket" {
  description = "S3 bucket holding canary run artifacts (1-day expiry, force_destroy)"
  value       = aws_s3_bucket.canary.bucket
}
