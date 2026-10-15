# ---------------------------------------------------------------------------
# Lab 08 app stack — "I break it on purpose, it fixes itself."
#
#   main.tf          VPC (2 public subnets, IGW, NO NAT), ALB, launch template, ASG
#   iam.tf           instance role (SSM), canary role, FIS role, remediation roles
#   observability.tf Synthetics canary + artifact bucket, the four CloudWatch alarms
#   remediation.tf   SSM Automation runbook + EventBridge rules (alarm -> runbook)
#   chaos.tf         three FIS experiment templates, stop condition = canary alarm
#   outputs.tf       what exercise.sh, chaos.sh and the workflows read
#
# Self-healing layers, innermost first (the README explains each):
#   1. systemd Restart=always         a crashed app process is back in ~1 s
#   2. ALB health check (5 s)         a sick instance stops getting traffic
#   3. ASG health_check_type = ELB    a dead/unhealthy instance is replaced
#   4. alarm -> EventBridge -> SSM    CPU saturation adds capacity, then removes it
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

# Latest Amazon Linux 2023 AMI via DescribeImages (EC2 permissions) rather than the public
# SSM parameter: lab 02's first real run failed on ssm:GetParameter.
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023*-kernel-*-x86_64"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

locals {
  # Stable names (no run_id) because CI re-applies this stack in place; run_id is a tag.
  name       = "${var.project}-lab08"
  azs        = slice(data.aws_availability_zones.available.names, 0, 2)
  partition  = data.aws_partition.current.partition
  account_id = data.aws_caller_identity.current.account_id

  # Tags the ASG stamps on every instance it launches. default_tags do NOT reach
  # ASG-launched instances, and FIS selects its targets by the lab08-role tag.
  instance_tags = {
    Name       = "${local.name}-web"
    project    = var.project
    lab        = "08-self-healing-chaos"
    stack      = "app"
    run_id     = var.run_id
    lab08-role = "web"
  }
}

# ---------------------------------------------------------------------------
# Network: 2 public subnets in 2 AZs + IGW. No NAT gateway ($0.045/h would be
# a third of the bill): instances have public IPs for dnf + the SSM endpoints,
# and the security group only admits the ALB.
# ---------------------------------------------------------------------------
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${local.name}-vpc" }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-igw" }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index) # 10.80.0.0/24, 10.80.1.0/24
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.name}-public-${count.index}", tier = "public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
  tags = { Name = "${local.name}-rt-public" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ---------------------------------------------------------------------------
# Security groups: internet -> ALB :80, ALB -> instances :app_port. No SSH:
# shell access (and every fault) goes through SSM.
# ---------------------------------------------------------------------------
resource "aws_security_group" "alb" {
  name_prefix = "${local.name}-alb-"
  description = "ALB: inbound HTTP from anywhere, all outbound"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP from anywhere (the canary runs outside the VPC)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    description = "All outbound (to the targets)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${local.name}-alb-sg" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "instance" {
  name_prefix = "${local.name}-web-"
  description = "Web instances: app port ONLY from the ALB, all outbound (dnf, SSM)"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "App port from the ALB only"
    from_port       = var.app_port
    to_port         = var.app_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    description = "All outbound: package repos + SSM/EC2 messages endpoints over the IGW"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${local.name}-web-sg" }

  lifecycle {
    create_before_destroy = true
  }
}

# ---------------------------------------------------------------------------
# ALB. Health check every 5 s, 2 failures = unhealthy: an instance that stops
# answering leaves rotation in ~10 s. Deregistration delay 10 s (default 300)
# so a scale-in or replacement does not sit draining for five minutes.
# ---------------------------------------------------------------------------
resource "aws_lb" "web" {
  name               = "${local.name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
  tags               = { Name = "${local.name}-alb" }
}

resource "aws_lb_target_group" "web" {
  name                 = "${local.name}-tg"
  port                 = var.app_port
  protocol             = "HTTP"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 10

  health_check {
    path                = "/healthz"
    protocol            = "HTTP"
    matcher             = "200"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 5
    timeout             = 2
  }
  tags = { Name = "${local.name}-tg" }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.web.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

# ---------------------------------------------------------------------------
# Launch template + ASG. health_check_type = ELB: the ASG replaces an instance
# the ALB calls unhealthy, not only one EC2 calls stopped. Detailed monitoring
# gives 1-minute CPU datapoints (5-minute basic metrics would add minutes to
# every time-to-detect this lab measures).
# ---------------------------------------------------------------------------
resource "aws_launch_template" "web" {
  name_prefix            = "${local.name}-lt-"
  image_id               = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.instance.id]
  update_default_version = true
  user_data = base64encode(templatefile("${path.module}/app/user_data.sh.tftpl", {
    app_py   = file("${path.module}/app/app.py")
    app_port = var.app_port
  }))

  iam_instance_profile {
    arn = aws_iam_instance_profile.web.arn
  }

  monitoring {
    enabled = true
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 1
  }

  # "unlimited" on purpose. A fresh t3 in STANDARD mode starts with ~0 credits and is
  # throttled to its 10 % baseline under stress, so CPUUtilization would plateau near 10 %
  # and the 80 % alarm could never fire: the experiment would "prove" a detector that is
  # blind. Unlimited bills surplus credits instead: 3 min x 2 vCPU x 90 % above baseline
  # x $0.05/vCPU-h is about $0.005 per instance per stress run.
  credit_specification {
    cpu_credits = "unlimited"
  }

  tag_specifications {
    resource_type = "instance"
    tags          = local.instance_tags
  }
  tag_specifications {
    resource_type = "volume"
    tags          = local.instance_tags
  }
  tags = { Name = "${local.name}-lt" }
}

resource "aws_autoscaling_group" "web" {
  name                      = "${local.name}-asg"
  min_size                  = var.baseline_capacity
  max_size                  = var.max_capacity
  desired_capacity          = var.baseline_capacity
  vpc_zone_identifier       = aws_subnet.public[*].id
  target_group_arns         = [aws_lb_target_group.web.arn]
  health_check_type         = "ELB"
  health_check_grace_period = 90 # boot + dnf + app start; ELB verdicts are ignored until then
  default_instance_warmup   = 60
  wait_for_capacity_timeout = "10m"
  min_elb_capacity          = var.baseline_capacity # apply waits until targets are HEALTHY, not just launched

  # Free 1-minute group metrics: the evidence shows desired vs in-service over time.
  enabled_metrics = [
    "GroupDesiredCapacity",
    "GroupInServiceInstances",
    "GroupPendingInstances",
    "GroupTerminatingInstances",
  ]

  launch_template {
    id      = aws_launch_template.web.id
    version = aws_launch_template.web.latest_version
  }

  dynamic "tag" {
    for_each = local.instance_tags
    content {
      key                 = tag.key
      value               = tag.value
      propagate_at_launch = false # the launch template's tag_specifications tag the instances
    }
  }

  lifecycle {
    # The scale-out runbook changes desired capacity at run time; a re-apply must
    # not "fix" that back while an experiment is in flight.
    ignore_changes = [desired_capacity]
  }
}
