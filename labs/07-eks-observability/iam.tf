# ---------------------------------------------------------------------------
# Four identities, four jobs:
#   cluster role  -> the EKS control plane: manages ENIs in our subnets and, via
#                    the in-tree cloud provider, creates the NLB for a Service of
#                    type LoadBalancer (that is why AmazonEKSClusterPolicy carries
#                    elasticloadbalancing:*; no extra controller needed).
#   node role     -> the kubelet on each t3.small: join the cluster, run the VPC
#                    CNI, pull images from ECR (PullOnly, not ReadOnly: no
#                    Describe*/List* of every repo in the account).
#   OIDC provider -> IAM trusts the cluster's own token issuer. This is IRSA's
#                    hinge: a pod's projected service-account token becomes AWS
#                    credentials via sts:AssumeRoleWithWebIdentity, exactly the
#                    mechanism GitHub Actions uses in lab 06, with the cluster
#                    as the identity provider instead of GitHub.
#   app role      -> what ONE service account (lab06/lab06-app) may do:
#                    describe images in lab 06's ECR repo, nothing else. The app
#                    never calls AWS; the role exists so the smoke test can
#                    prove the webhook injected the credentials (AWS_ROLE_ARN +
#                    token file in the pod) and the trust is pinned to one SA.
# ---------------------------------------------------------------------------

# --- cluster role -----------------------------------------------------------
data "aws_iam_policy_document" "eks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
    # Confused-deputy guard: only clusters in THIS account may assume the role.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "cluster" {
  name               = "${local.name}-cluster"
  description        = "EKS control plane for ${local.cluster_name}"
  assume_role_policy = data.aws_iam_policy_document.eks_assume.json
  tags               = { Name = "${local.name}-cluster" }
}

resource "aws_iam_role_policy_attachment" "cluster_policy" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonEKSClusterPolicy"
}

# --- node role --------------------------------------------------------------
data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${local.name}-node"
  description        = "Kubelet on the ${local.cluster_name} managed node group"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
  tags               = { Name = "${local.name}-node" }
}

resource "aws_iam_role_policy_attachment" "node_worker" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

# The VPC CNI runs on the node role here. The next step up is giving aws-node its
# own IRSA role (vpc-cni add-on + service_account_role_arn) so pods on the node
# cannot borrow ec2:AssignPrivateIpAddresses through the instance profile.
resource "aws_iam_role_policy_attachment" "node_cni" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "node_ecr_pull" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonEC2ContainerRegistryPullOnly"
}

# --- cluster OIDC provider (IRSA) -------------------------------------------
resource "aws_iam_openid_connect_provider" "cluster" {
  url             = aws_eks_cluster.main.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [var.oidc_thumbprint]
  tags            = { Name = "${local.cluster_name}-oidc" }
}

locals {
  # "oidc.eks.us-east-1.amazonaws.com/id/EXAMPLE" — the condition-key prefix
  oidc_issuer_host = replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")
}

# --- IRSA role for the app service account ----------------------------------
data "aws_iam_policy_document" "app_irsa_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.cluster.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_issuer_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
    # ONE namespace, ONE service account. Any other pod's token is refused.
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_issuer_host}:sub"
      values   = ["system:serviceaccount:${var.app_namespace}:${var.app_service_account}"]
    }
  }
}

resource "aws_iam_role" "app" {
  name               = "${local.name}-app-irsa"
  description        = "IRSA: assumed only by the ${var.app_namespace}/${var.app_service_account} service account in ${local.cluster_name}"
  assume_role_policy = data.aws_iam_policy_document.app_irsa_trust.json
  tags               = { Name = "${local.name}-app-irsa" }
}

data "aws_iam_policy_document" "app_irsa" {
  statement {
    sid       = "DescribeOwnImageRepoOnly"
    actions   = ["ecr:DescribeImages", "ecr:ListImages", "ecr:DescribeRepositories"]
    resources = [data.aws_ecr_repository.app.arn]
  }
}

resource "aws_iam_role_policy" "app" {
  name   = "${local.name}-app-irsa"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app_irsa.json
}
