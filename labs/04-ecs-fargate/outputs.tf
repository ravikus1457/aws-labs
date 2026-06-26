output "cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "service_name" {
  description = "ECS service name"
  value       = aws_ecs_service.web.name
}

output "task_family" {
  description = "Task definition family"
  value       = aws_ecs_task_definition.web.family
}

output "vpc_id" {
  description = "ID of the lab VPC"
  value       = aws_vpc.main.id
}

output "security_group_id" {
  description = "Security group attached to the Fargate task"
  value       = aws_security_group.web.id
}

output "subnet_ids" {
  description = "Public subnet IDs the task runs in"
  value       = aws_subnet.public[*].id
}
