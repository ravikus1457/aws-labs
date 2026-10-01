output "alb_dns_name" {
  description = "Public DNS name of the ALB"
  value       = aws_lb.app.dns_name
}

output "alb_url" {
  description = "Base URL to hit (/healthz, /version)"
  value       = "${var.enable_https ? "https" : "http"}://${aws_lb.app.dns_name}"
}

output "target_group_arn" {
  description = "ALB target group (exercise.sh checks 2 healthy targets here)"
  value       = aws_lb_target_group.app.arn
}

output "cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "service_name" {
  description = "ECS service name"
  value       = aws_ecs_service.app.name
}

output "desired_count" {
  description = "Tasks the service keeps running"
  value       = var.desired_count
}

output "image_uri" {
  description = "Exact image the task definition runs"
  value       = local.image_uri
}

output "image_tag" {
  description = "Image tag deployed (the git SHA when CI deployed it)"
  value       = var.image_tag
}

output "ecr_repository_name" {
  description = "ECR repository the image came from"
  value       = data.aws_ecr_repository.app.name
}

output "task_definition_arn" {
  description = "Task definition revision in use"
  value       = aws_ecs_task_definition.app.arn
}

output "log_group_name" {
  description = "CloudWatch log group for container stdout"
  value       = aws_cloudwatch_log_group.app.name
}

output "sns_topic_arn" {
  description = "Alarm notifications land here"
  value       = aws_sns_topic.alerts.arn
}

output "alarm_names" {
  description = "CloudWatch alarms wired to the topic"
  value       = [aws_cloudwatch_metric_alarm.alb_5xx_rate.alarm_name, aws_cloudwatch_metric_alarm.unhealthy_hosts.alarm_name]
}

output "vpc_id" {
  description = "ID of the lab VPC"
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "Private subnets the tasks run in"
  value       = aws_subnet.private[*].id
}

output "nat_gateway_id" {
  description = "The single NAT Gateway (the main hourly cost while the lab is up)"
  value       = aws_nat_gateway.nat.id
}
