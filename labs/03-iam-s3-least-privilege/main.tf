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
      lab        = "03-iam-s3-least-privilege"
      managed_by = "terraform"
    }
  }
}

locals {
  name = "${var.project}-${var.run_id}"
}

# Who am I? Used to scope the role's trust policy to THIS account only.
data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# S3 bucket — the resource we are protecting.
# Name is globally unique via project+run_id. force_destroy lets the runner
# tear it down even if objects exist.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "data" {
  bucket        = "${var.project}-${var.run_id}-data"
  force_destroy = true
  tags          = { Name = "${local.name}-data" }
}

# Block ALL public access — private bucket, no exceptions.
resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Versioning — keep object history (recover from overwrite/delete).
resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

# ---------------------------------------------------------------------------
# IAM role an application would assume. Trust policy allows ONLY this AWS
# account to assume it (no external principals, no long-lived user keys).
# ---------------------------------------------------------------------------
resource "aws_iam_role" "app" {
  name = "${local.name}-app-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowAccountAssume"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
  tags = { Name = "${local.name}-app-role" }
}

# ---------------------------------------------------------------------------
# Least-privilege policy: read/write OBJECTS in the bucket and list it.
# Nothing else — no DeleteBucket, no other services, no other buckets.
# ---------------------------------------------------------------------------
resource "aws_iam_policy" "app" {
  name        = "${local.name}-app-policy"
  description = "Least-privilege S3 access for ${local.name} app role"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadWriteObjects"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject"
        ]
        Resource = "${aws_s3_bucket.data.arn}/*"
      },
      {
        Sid      = "ListBucket"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.data.arn
      }
    ]
  })
  tags = { Name = "${local.name}-app-policy" }
}

resource "aws_iam_role_policy_attachment" "app" {
  role       = aws_iam_role.app.name
  policy_arn = aws_iam_policy.app.arn
}
