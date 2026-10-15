terraform {
  required_version = ">= 1.10" # S3-native state locking (use_lockfile) landed in 1.10
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0" # locked to 5.100.0 in .terraform.lock.hcl (same build labs 06/07 use)
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4" # zips the canary script; no network, no AWS calls
    }
  }
  # Backend: intentionally NOT declared here. Drop a `backend.tf` (git-ignored;
  # see backend.tf.example) next to this file to use the lab 06 state bucket
  # under its own key (lab08/terraform.tfstate). CI writes the same file.
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      project    = var.project
      run_id     = var.run_id
      lab        = "08-self-healing-chaos"
      stack      = "app" # the nightly destroy verifies nothing with stack=app survives
      managed_by = "terraform"
    }
  }
}
