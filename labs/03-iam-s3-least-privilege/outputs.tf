output "bucket_name" {
  description = "Name of the protected S3 bucket"
  value       = aws_s3_bucket.data.bucket
}

output "bucket_arn" {
  description = "ARN of the protected S3 bucket"
  value       = aws_s3_bucket.data.arn
}

output "role_arn" {
  description = "ARN of the least-privilege application IAM role"
  value       = aws_iam_role.app.arn
}

output "policy_arn" {
  description = "ARN of the least-privilege IAM policy attached to the role"
  value       = aws_iam_policy.app.arn
}
