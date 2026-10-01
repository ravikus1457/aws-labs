# ---------------------------------------------------------------------------
# VPC: 2 public subnets (NLB + NAT) and 2 private subnets (nodes + control-
# plane ENIs). One NAT Gateway shared by both private subnets; nodes need
# egress to pull images (ECR, quay.io for the monitoring stack) and to reach
# the EKS/STS/CloudWatch APIs. Same shape as lab 06, different CIDR.
#
# The kubernetes.io/* subnet tags are how the in-cluster AWS cloud provider
# finds subnets for a Service of type LoadBalancer: role/elb for an internet-
# facing NLB, role/internal-elb for an internal one.
# ---------------------------------------------------------------------------
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true # required by EKS (nodes join by DNS name)
  tags                 = { Name = "${local.name}-vpc" }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-igw" }
}

# Public: 10.70.0.0/24, 10.70.1.0/24
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true
  tags = {
    Name                                          = "${local.name}-public-${count.index}"
    tier                                          = "public"
    "kubernetes.io/role/elb"                      = "1"
    "kubernetes.io/cluster/${local.cluster_name}" = "shared"
  }
}

# Private: 10.70.10.0/24, 10.70.11.0/24 — nodes live here, no public IPs, ever.
resource "aws_subnet" "private" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 10 + count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = false
  tags = {
    Name                                          = "${local.name}-private-${count.index}"
    tier                                          = "private"
    "kubernetes.io/role/internal-elb"             = "1"
    "kubernetes.io/cluster/${local.cluster_name}" = "shared"
  }
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

# Single NAT in the first public subnet. Trade-off (documented): if that AZ goes
# down, nodes in the other AZ lose egress but keep SERVING (the NLB path does
# not use the NAT). A NAT per AZ doubles the cost for a lab.
resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${local.name}-nat-eip" }
}

resource "aws_nat_gateway" "nat" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = { Name = "${local.name}-nat" }
  depends_on    = [aws_internet_gateway.igw]
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.nat.id
  }
  tags = { Name = "${local.name}-rt-private" }
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}
