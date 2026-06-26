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
      lab        = "04-ecs-fargate"
      managed_by = "terraform"
    }
  }
}

locals {
  name = "${var.project}-${var.run_id}"
}

# Two AZs so the service can be scheduled across a realistic, HA layout.
data "aws_availability_zones" "available" {
  state = "available"
}

# ---------------------------------------------------------------------------
# VPC — its own network. Public subnets only: the Fargate task runs in a
# public subnet with a public IP so it can pull the image and be reached.
# No NAT Gateway (the expensive bit) — keeps the lab near zero cost.
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
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index) # 10.40.0.0/24, 10.40.1.0/24
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.name}-public-${count.index}", tier = "public" }
}

# Public route table: 0.0.0.0/0 -> Internet Gateway
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
# Security group — HTTP in from anywhere, all egress (egress is needed to
# pull the image from the public registry).
# ---------------------------------------------------------------------------
resource "aws_security_group" "web" {
  name_prefix = "${local.name}-web-"
  description = "Allow inbound HTTP, all outbound"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP from anywhere"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${local.name}-web-sg" }
}

# ---------------------------------------------------------------------------
# CloudWatch log group — the container's stdout/stderr lands here via the
# awslogs driver. retention_in_days = 1 keeps it cheap and fully destroyable.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "web" {
  name              = "/ecs/${local.name}"
  retention_in_days = 1
  tags              = { Name = "${local.name}-logs" }
}

# ---------------------------------------------------------------------------
# Task execution role — used by the ECS agent (NOT your app) to pull the
# image and push logs to CloudWatch. Trusts the ecs-tasks service principal.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-ecs-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = { Name = "${local.name}-ecs-exec" }
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ---------------------------------------------------------------------------
# ECS cluster — the logical grouping the service/tasks run in.
# ---------------------------------------------------------------------------
resource "aws_ecs_cluster" "main" {
  name = "${local.name}-cluster"
  tags = { Name = "${local.name}-cluster" }
}

# ---------------------------------------------------------------------------
# Task definition — the blueprint: what image, CPU/memory, ports, logging.
# Fargate requires awsvpc networking and the FARGATE compatibility.
# ---------------------------------------------------------------------------
resource "aws_ecs_task_definition" "web" {
  family                   = "${local.name}-web"
  cpu                      = "256"
  memory                   = "512"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  execution_role_arn       = aws_iam_role.execution.arn

  container_definitions = jsonencode([
    {
      name      = "web"
      image     = var.container_image
      essential = true
      portMappings = [
        {
          containerPort = 80
          protocol      = "tcp"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.web.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "web"
        }
      }
    }
  ])

  tags = { Name = "${local.name}-web" }
}

# ---------------------------------------------------------------------------
# ECS service — the controller: keeps desired_count tasks running, replacing
# any that die. Launches on Fargate in the public subnets with a public IP.
# ---------------------------------------------------------------------------
resource "aws_ecs_service" "web" {
  name            = "${local.name}-svc"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.web.id]
    assign_public_ip = true
  }

  tags = { Name = "${local.name}-svc" }

  depends_on = [aws_iam_role_policy_attachment.execution]
}
