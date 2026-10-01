# ---------------------------------------------------------------------------
# CloudWatch log group — container stdout (one JSON line per request).
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.name}"
  retention_in_days = var.log_retention_days
  tags              = { Name = "${local.name}-logs" }
}

# ---------------------------------------------------------------------------
# Cluster. Container Insights stays OFF: it is the one ECS feature with a real
# per-task metrics charge, and the ALB + service metrics below are free.
# ---------------------------------------------------------------------------
resource "aws_ecs_cluster" "main" {
  name = "${local.name}-cluster"
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
  tags = { Name = "${local.name}-cluster" }
}

# ---------------------------------------------------------------------------
# Task definition — immutable blueprint. Every CI apply registers a new
# revision (new image tag); the service rolls to it.
# ---------------------------------------------------------------------------
resource "aws_ecs_task_definition" "app" {
  family                   = "${local.name}-app"
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory)
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64" # CI builds on amd64 runners; match it
  }

  container_definitions = jsonencode([
    {
      name                   = "app"
      image                  = local.image_uri
      essential              = true
      user                   = "10001:10001" # belt-and-braces with the Dockerfile's USER
      readonlyRootFilesystem = true          # the app writes nothing to disk
      portMappings = [
        {
          containerPort = var.container_port
          protocol      = "tcp"
        }
      ]
      environment = [
        { name = "PORT", value = tostring(var.container_port) },
        { name = "APP_VERSION", value = var.image_tag },
      ]
      healthCheck = {
        command     = ["CMD-SHELL", "python3 -c \"import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.container_port}/healthz', timeout=2).status == 200 else 1)\""]
        interval    = 15
        timeout     = 5
        retries     = 3
        startPeriod = 10
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "app"
        }
      }
    }
  ])

  tags = { Name = "${local.name}-app" }
}

# ---------------------------------------------------------------------------
# Service — keeps desired_count tasks running across the two private subnets,
# registered with the ALB target group. Rolling deploy with the circuit
# breaker: if the new revision never passes health checks, ECS rolls back
# instead of leaving the service half-dead. wait_for_steady_state makes
# `terraform apply` block until that verdict is in, so CI's apply step is the
# deployment gate, not just "API accepted the request".
# ---------------------------------------------------------------------------
resource "aws_ecs_service" "app" {
  name                               = "${local.name}-svc"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.app.arn
  desired_count                      = var.desired_count
  launch_type                        = "FARGATE"
  platform_version                   = "LATEST"
  health_check_grace_period_seconds  = 60
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  wait_for_steady_state              = true
  enable_execute_command             = false

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false # private subnets; egress goes through the NAT
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = var.container_port
  }

  tags = { Name = "${local.name}-svc" }

  # The listener must exist before the service registers targets, and the
  # NAT route must exist or the first image pull hangs.
  depends_on = [
    aws_lb_listener.http,
    aws_route_table_association.private,
    aws_iam_role_policy.execution,
  ]

  timeouts {
    create = "15m"
    update = "15m"
    delete = "15m"
  }
}
