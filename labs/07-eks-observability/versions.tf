terraform {
  required_version = ">= 1.10" # S3-native state locking (use_lockfile) landed in 1.10
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0" # locked to 5.100.0 in .terraform.lock.hcl (same build lab 06 uses)
    }
  }
  # Backend: intentionally NOT declared here. Drop a `backend.tf` (git-ignored;
  # see backend.tf.example) next to this file to use the lab 06 state bucket
  # under its own key (lab07/terraform.tfstate). Without it Terraform uses
  # local state, which is what scripts/run-lab.sh expects for a throw-away run.
  # The two labs never share a state file: same bucket, different key.
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      project    = var.project
      run_id     = var.run_id
      lab        = "07-eks-observability"
      stack      = "app" # the nightly destroy verifies nothing with stack=app survives
      managed_by = "terraform"
    }
  }
}
