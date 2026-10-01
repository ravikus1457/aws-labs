# ---------------------------------------------------------------------------
# Security groups. Rules are separate resources (not inline blocks) so the ALB
# SG can reference the task SG and vice-versa without a dependency cycle.
# ---------------------------------------------------------------------------
resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "ALB: HTTP(S) from the internet, egress only to the tasks"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${local.name}-alb" }
}

resource "aws_security_group" "tasks" {
  name        = "${local.name}-tasks"
  description = "Fargate tasks: app port from the ALB only, all egress via NAT"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${local.name}-tasks" }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from anywhere"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  count             = var.enable_https ? 1 : 0
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from anywhere"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_tasks" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Only to the tasks on the app port"
  referenced_security_group_id = aws_security_group.tasks.id
  from_port                    = var.container_port
  to_port                      = var.container_port
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "tasks_from_alb" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "App port from the ALB SG only (never from the internet)"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = var.container_port
  to_port                      = var.container_port
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "tasks_all" {
  security_group_id = aws_security_group.tasks.id
  description       = "Outbound (ECR pull + CloudWatch Logs via NAT)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# ---------------------------------------------------------------------------
# Application Load Balancer — public subnets, L7, health-checks /healthz.
# ---------------------------------------------------------------------------
resource "aws_lb" "app" {
  name                       = "${local.name}-alb"
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = aws_subnet.public[*].id
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  enable_deletion_protection = false # lab: must be destroyable by the nightly job
  idle_timeout               = 60
  tags                       = { Name = "${local.name}-alb" }
}

resource "aws_lb_target_group" "app" {
  name                 = "${local.name}-tg"
  port                 = var.container_port
  protocol             = "HTTP"
  target_type          = "ip" # Fargate tasks register by ENI IP, not instance id
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 10 # seconds; fast rollouts + fast teardown for a lab

  health_check {
    path                = "/healthz"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
  tags = { Name = "${local.name}-tg" }
}

# :80 forwards to the tasks, or redirects to :443 when HTTPS is on.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  dynamic "default_action" {
    for_each = var.enable_https ? [] : [1]
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.app.arn
    }
  }
  dynamic "default_action" {
    for_each = var.enable_https ? [1] : []
    content {
      type = "redirect"
      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
  tags = { Name = "${local.name}-http" }
}

resource "aws_lb_listener" "https" {
  count             = var.enable_https ? 1 : 0
  load_balancer_arn = aws_lb.app.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
  tags = { Name = "${local.name}-https" }

  lifecycle {
    precondition {
      condition     = var.acm_certificate_arn != ""
      error_message = "enable_https = true requires acm_certificate_arn."
    }
  }
}
