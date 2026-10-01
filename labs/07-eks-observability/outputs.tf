output "cluster_name" {
  description = "EKS cluster name (aws eks update-kubeconfig --name <this>)"
  value       = aws_eks_cluster.main.name
}

output "cluster_endpoint" {
  description = "Kubernetes API endpoint"
  value       = aws_eks_cluster.main.endpoint
}

output "cluster_version" {
  description = "Kubernetes version the control plane is running"
  value       = aws_eks_cluster.main.version
}

output "cluster_platform_version" {
  description = "EKS platform version (eks.N) — changes when AWS patches the control plane"
  value       = aws_eks_cluster.main.platform_version
}

output "aws_region" {
  description = "Region the cluster lives in (exercise.sh reads it from here when not exported)"
  value       = var.aws_region
}

output "kubeconfig_command" {
  description = "Run this to point kubectl at the cluster (IAM identity -> access entry -> RBAC)"
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${aws_eks_cluster.main.name}"
}

output "node_group_name" {
  description = "Managed node group"
  value       = aws_eks_node_group.main.node_group_name
}

output "node_count" {
  description = "Nodes the group keeps running (exercise.sh expects this many Ready)"
  value       = var.node_count
}

output "node_instance_type" {
  description = "Instance type of the nodes"
  value       = var.node_instance_type
}

output "oidc_provider_arn" {
  description = "IAM OIDC provider for the cluster (IRSA)"
  value       = aws_iam_openid_connect_provider.cluster.arn
}

output "oidc_issuer" {
  description = "The cluster's OIDC issuer URL (what the IRSA trust policy keys on)"
  value       = aws_eks_cluster.main.identity[0].oidc[0].issuer
}

output "app_irsa_role_arn" {
  description = "IRSA role for the app service account — passed to the chart as serviceAccount.roleArn"
  value       = aws_iam_role.app.arn
}

output "app_namespace" {
  description = "Namespace the chart is installed into (the IRSA trust is pinned to it)"
  value       = var.app_namespace
}

output "app_service_account" {
  description = "Service account the IRSA trust is pinned to"
  value       = var.app_service_account
}

output "ecr_repository_url" {
  description = "Lab 06 ECR repository URL — the chart's image.repository"
  value       = data.aws_ecr_repository.app.repository_url
}

output "ecr_repository_name" {
  description = "Lab 06 ECR repository name"
  value       = data.aws_ecr_repository.app.name
}

output "metrics_server_addon_version" {
  description = "metrics-server EKS add-on version installed (empty if disabled)"
  value       = var.metrics_server_addon ? aws_eks_addon.metrics_server[0].addon_version : ""
}

output "cluster_log_group_name" {
  description = "CloudWatch log group for the enabled control-plane log types"
  value       = aws_cloudwatch_log_group.cluster.name
}

output "cluster_log_types" {
  description = "Control-plane log types shipped to CloudWatch"
  value       = var.cluster_log_types
}

output "admin_principal_arns" {
  description = "IAM principals granted cluster-admin through access entries (the cluster creator is implicit)"
  value       = sort(tolist(local.admin_principals))
}

output "vpc_id" {
  description = "ID of the lab VPC"
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "Private subnets the nodes run in"
  value       = aws_subnet.private[*].id
}

output "public_subnet_ids" {
  description = "Public subnets the NLB is placed in (tagged kubernetes.io/role/elb)"
  value       = aws_subnet.public[*].id
}

output "nat_gateway_id" {
  description = "The single NAT Gateway (second-largest hourly line after the control plane)"
  value       = aws_nat_gateway.nat.id
}

output "cluster_security_group_id" {
  description = "EKS-managed cluster security group (nodes + control plane; Kubernetes adds NLB rules to it)"
  value       = aws_eks_cluster.main.vpc_config[0].cluster_security_group_id
}
