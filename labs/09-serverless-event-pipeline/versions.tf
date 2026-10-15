terraform {
  required_version = ">= 1.10" # S3-native state locking (use_lockfile) landed in 1.10
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0" # same major as labs 06/07; exact build pinned in .terraform.lock.hcl
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4" # zips src/ into the Lambda deployment package
    }
  }
  # Backend: intentionally NOT declared here (same pattern as lab 07). CI writes a
  # backend.tf pointing at lab 06's state bucket under key lab09/terraform.tfstate;
  # locally, copy backend.tf.example or run with local state.
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      project    = var.project
      run_id     = var.run_id
      lab        = "09-serverless-event-pipeline"
      stack      = "app" # the nightly destroy verifies nothing from this stack survives
      managed_by = "terraform"
    }
  }
}
