output "deploy_role_arn" {
  description = "The (lab 06) role that now also carries the lab 07 policy — already the AWS_DEPLOY_ROLE_ARN repo variable"
  value       = data.aws_iam_role.deploy.arn
}

output "policy_name" {
  description = "Inline policy added to the deploy role"
  value       = aws_iam_role_policy.lab07.name
}

output "backend_tf" {
  description = "Paste into labs/07-eks-observability/backend.tf (git-ignored) to use remote state locally"
  value       = <<-EOT
    terraform {
      backend "s3" {
        bucket       = "${var.tf_state_bucket}"
        key          = "lab07/terraform.tfstate"
        region       = "${var.aws_region}"
        encrypt      = true
        use_lockfile = true
      }
    }
  EOT
}

output "github_cli_commands" {
  description = "The lab 07 workflows reuse lab 06's repo variables; the only new GitHub object is the approval environment"
  value       = <<-EOT
    # approval gate: an environment with YOU as required reviewer (same pattern as lab06-production)
    gh api --method PUT repos/ravikus1457/aws-labs/environments/lab07-production \
      --input - <<< "{\"reviewers\":[{\"type\":\"User\",\"id\":$(gh api user -q .id)}]}"
    # repo variables AWS_REGION, AWS_DEPLOY_ROLE_ARN, TF_STATE_BUCKET, ECR_REPOSITORY were set by lab 06's bootstrap
  EOT
}
