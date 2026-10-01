terraform {
  required_version = ">= 1.10" # S3-native state locking (use_lockfile) landed in 1.10
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # Backend: intentionally NOT declared here. Drop a `backend.tf` (git-ignored;
  # see backend.tf.example or the bootstrap stack's `backend_tf` output) next to
  # this file to use the S3 remote state. Without it Terraform uses local state,
  # which is what scripts/run-lab.sh expects for a throw-away local run.
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      project    = var.project
      run_id     = var.run_id
      lab        = "06-ecs-fargate-cicd"
      stack      = "app" # the nightly destroy verifies nothing with stack=app survives
      managed_by = "terraform"
    }
  }
}
