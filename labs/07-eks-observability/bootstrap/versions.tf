terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # Local state ON PURPOSE, like lab 06's bootstrap: one inline policy, free,
  # re-creatable from this file in seconds if the state is lost.
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      project    = var.project
      run_id     = var.run_id
      lab        = "07-eks-observability"
      stack      = "bootstrap"
      managed_by = "terraform"
    }
  }
}
