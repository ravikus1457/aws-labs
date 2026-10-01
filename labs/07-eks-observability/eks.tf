# ---------------------------------------------------------------------------
# Control-plane log group — created HERE, before the cluster, with a retention.
# If EKS creates /aws/eks/<name>/cluster itself the group never expires and
# survives terraform destroy: the classic "why is there still a log group"
# leftover. Owning it means destroy removes it.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "cluster" {
  name              = "/aws/eks/${local.cluster_name}/cluster"
  retention_in_days = var.log_retention_days
  tags              = { Name = "${local.cluster_name}-logs" }
}

# ---------------------------------------------------------------------------
# Cluster. $0.10/h the moment it exists, whatever runs in it. Choices:
#   - access_config API: IAM principals map to Kubernetes RBAC through EKS
#     *access entries* (an AWS API), not the aws-auth ConfigMap (a kubectl edit
#     that locked people out of clusters for years).
#   - upgrade_policy STANDARD: when this version leaves standard support EKS
#     auto-upgrades the control plane instead of silently billing $0.60/h
#     extended support. A lab that lives one hour never notices; a forgotten
#     one is protected from the 6x surprise.
#   - public endpoint ON (GitHub runners need it), private endpoint ON (nodes
#     and the control plane talk inside the VPC, not via the NAT).
#   - encryption of secrets with a KMS key: skipped. EKS >= 1.28 encrypts etcd
#     at rest with an AWS-owned key by default; a CMK adds $1/month + a key to
#     destroy. Mention it in the interview, don't pay for it in a lab.
# ---------------------------------------------------------------------------
resource "aws_eks_cluster" "main" {
  name     = local.cluster_name
  version  = var.kubernetes_version
  role_arn = aws_iam_role.cluster.arn

  vpc_config {
    subnet_ids              = aws_subnet.private[*].id # control-plane ENIs land here
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.endpoint_public_access_cidrs
  }

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = true # whoever applies can kubectl immediately
  }

  upgrade_policy {
    support_type = "STANDARD"
  }

  kubernetes_network_config {
    ip_family = "ipv4"
  }

  # vpc-cni, coredns, kube-proxy are installed by EKS as self-managed add-ons.
  # Fine for a lab; managing them as aws_eks_addon resources is the upgrade.
  bootstrap_self_managed_addons = true

  enabled_cluster_log_types = var.cluster_log_types

  tags = { Name = local.cluster_name }

  depends_on = [
    aws_iam_role_policy_attachment.cluster_policy,
    aws_cloudwatch_log_group.cluster,
  ]

  timeouts {
    create = "20m"
    update = "30m"
    delete = "20m"
  }
}

# ---------------------------------------------------------------------------
# ONE managed node group: 2 × t3.small across the two private subnets.
# Managed = EKS owns the ASG, the launch template, the AMI, and drains nodes
# on update/scale-in. No SSH (remote_access omitted): debugging goes through
# kubectl debug node/ or SSM, not port 22.
#
# Capacity maths (why these pods fit on 2 small nodes):
#   t3.small allows 11 pods/node (3 ENIs × 4 IPs − 1 = 11 with the VPC CNI's
#   default secondary-IP mode) -> 22 pod slots. System: aws-node×2,
#   kube-proxy×2, coredns×2, metrics-server×1 = 7. kube-prometheus-stack:
#   operator, prometheus, alertmanager, grafana, kube-state-metrics,
#   node-exporter×2 = 7. App: 2 (HPA max 4). Total 16-18 of 22.
#   Memory: 2 × ~1.4 GiB allocatable; the Helm values request ~900 MiB total.
#   Prefix delegation on the CNI would lift the pod cap to 110; not needed.
# ---------------------------------------------------------------------------
resource "aws_eks_node_group" "main" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${local.name}-ng"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = aws_subnet.private[*].id
  version         = var.kubernetes_version
  ami_type        = var.node_ami_type
  instance_types  = [var.node_instance_type]
  capacity_type   = var.node_capacity_type
  disk_size       = var.node_disk_gb

  scaling_config {
    desired_size = var.node_count
    min_size     = var.node_count
    max_size     = var.node_max
  }

  update_config {
    max_unavailable = 1
  }

  labels = {
    "awslabs/lab"  = "07"
    "awslabs/role" = "general"
  }

  tags = { Name = "${local.name}-ng" }

  # Role policies must be attached before the kubelet tries to join, and the
  # NAT route must exist or the first image pull hangs.
  depends_on = [
    aws_iam_role_policy_attachment.node_worker,
    aws_iam_role_policy_attachment.node_cni,
    aws_iam_role_policy_attachment.node_ecr_pull,
    aws_route_table_association.private,
  ]

  lifecycle {
    ignore_changes = [scaling_config[0].desired_size] # a manual scale is not drift worth reverting
  }

  timeouts {
    create = "20m"
    update = "20m"
    delete = "20m"
  }
}

# ---------------------------------------------------------------------------
# metrics-server as an EKS community add-on (available since Nov 2024). The
# HPA's CPU target reads from the metrics API this serves; without it the HPA
# shows <unknown> forever. Add-on = EKS picks a version compatible with the
# cluster, destroys with the cluster, no Helm release to track.
# ---------------------------------------------------------------------------
data "aws_eks_addon_version" "metrics_server" {
  count              = var.metrics_server_addon ? 1 : 0
  addon_name         = "metrics-server"
  kubernetes_version = aws_eks_cluster.main.version
  most_recent        = true
}

resource "aws_eks_addon" "metrics_server" {
  count                       = var.metrics_server_addon ? 1 : 0
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "metrics-server"
  addon_version               = data.aws_eks_addon_version.metrics_server[0].version
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  tags                        = { Name = "${local.cluster_name}-metrics-server" }

  # Needs schedulable nodes or it sits in DEGRADED until the create timeout.
  depends_on = [aws_eks_node_group.main]

  timeouts {
    create = "15m"
    delete = "10m"
  }
}

# ---------------------------------------------------------------------------
# Access entries: cluster-admin for the CI deploy role and any named humans.
# The principal running the apply is skipped (EKS already made its entry).
# AmazonEKSClusterAdminPolicy at cluster scope == the cluster-admin ClusterRole.
# ---------------------------------------------------------------------------
resource "aws_eks_access_entry" "admin" {
  for_each      = local.admin_principals
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = each.value
  type          = "STANDARD"
  tags          = { Name = "${local.cluster_name}-admin" }
}

resource "aws_eks_access_policy_association" "admin" {
  for_each      = local.admin_principals
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = each.value
  policy_arn    = "arn:${local.partition}:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.admin]
}
