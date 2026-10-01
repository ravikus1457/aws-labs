output "tfstate_bucket" {
  description = "S3 bucket holding the app stack's remote state"
  value       = aws_s3_bucket.tfstate.bucket
}

output "ecr_repository_name" {
  description = "ECR repository name (the app stack looks it up by name)"
  value       = aws_ecr_repository.app.name
}

output "ecr_repository_url" {
  description = "ECR repository URL to push to"
  value       = aws_ecr_repository.app.repository_url
}

output "oidc_provider_arn" {
  description = "GitHub OIDC provider ARN"
  value       = local.oidc_arn
}

output "deploy_role_arn" {
  description = "Role GitHub Actions assumes (set as the AWS_DEPLOY_ROLE_ARN repo variable)"
  value       = aws_iam_role.deploy.arn
}

output "budget_name" {
  description = "AWS Budgets name"
  value       = aws_budgets_budget.lab.name
}

output "backend_tf" {
  description = "Paste into labs/06-ecs-fargate-cicd/backend.tf (git-ignored) to use remote state locally"
  value       = <<-EOT
    terraform {
      backend "s3" {
        bucket       = "${aws_s3_bucket.tfstate.bucket}"
        key          = "lab06/terraform.tfstate"
        region       = "${var.aws_region}"
        encrypt      = true
        use_lockfile = true
      }
    }
  EOT
}

output "github_cli_commands" {
  description = "Repo variables the workflows read. Run these once (gh must be authenticated)"
  value       = <<-EOT
    gh variable set AWS_REGION          --repo ${var.github_repo} --body "${var.aws_region}"
    gh variable set AWS_DEPLOY_ROLE_ARN --repo ${var.github_repo} --body "${aws_iam_role.deploy.arn}"
    gh variable set TF_STATE_BUCKET     --repo ${var.github_repo} --body "${aws_s3_bucket.tfstate.bucket}"
    gh variable set ECR_REPOSITORY      --repo ${var.github_repo} --body "${aws_ecr_repository.app.name}"
    gh variable set ALERT_EMAIL         --repo ${var.github_repo} --body "${var.alert_email}"
  EOT
}
