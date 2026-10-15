output "deploy_role_arn" {
  description = "The (lab 06) role that now also carries the lab 09 policy — already the AWS_DEPLOY_ROLE_ARN repo variable"
  value       = data.aws_iam_role.deploy.arn
}

output "policy_name" {
  description = "Policy added to the deploy role (inline or managed, see attach_as_managed_policy)"
  value       = var.attach_as_managed_policy ? aws_iam_policy.lab09[0].name : aws_iam_role_policy.lab09[0].name
}

output "policy_characters" {
  description = "Size of the policy document without whitespace (IAM's quota unit: 10,240 aggregate inline per role, 6,144 per managed policy)"
  value       = length(replace(data.aws_iam_policy_document.lab09.json, "/\\s/", ""))
}

output "backend_tf" {
  description = "Paste into labs/09-serverless-event-pipeline/backend.tf (git-ignored) to use remote state locally"
  value       = <<-EOT
    terraform {
      backend "s3" {
        bucket       = "${var.tf_state_bucket}"
        key          = "lab09/terraform.tfstate"
        region       = "${var.aws_region}"
        encrypt      = true
        use_lockfile = true
      }
    }
  EOT
}

output "github_cli_commands" {
  description = "The lab 09 workflows reuse lab 06's repo variables; the only new GitHub object is the approval environment"
  value       = <<-EOT
    # approval gate: an environment with YOU as required reviewer (same pattern as lab06/lab07-production)
    gh api --method PUT repos/ravikus1457/aws-labs/environments/lab09-production \
      --input - <<< "{\"reviewers\":[{\"type\":\"User\",\"id\":$(gh api user -q .id)}]}"
    # repo variables AWS_REGION, AWS_DEPLOY_ROLE_ARN, TF_STATE_BUCKET were set by lab 06's bootstrap
  EOT
}
