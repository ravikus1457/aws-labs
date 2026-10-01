terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # Local state ON PURPOSE: this stack creates the bucket the app stack's state
  # lives in. Keep bootstrap/terraform.tfstate (git-ignored) somewhere safe;
  # it only tracks ~10 free resources and can be re-imported if lost.
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      project    = var.project
      run_id     = var.run_id
      lab        = "06-ecs-fargate-cicd"
      stack      = "bootstrap"
      managed_by = "terraform"
    }
  }
}
