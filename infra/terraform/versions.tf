terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.48"
    }
  }

  # Configure a remote backend for real deployments, for example:
  #
  # backend "s3" {
  #   bucket       = "<your-state-bucket>"
  #   key          = "jit-access/terraform.tfstate"
  #   region       = "<region>"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = merge({ Project = "agentic-jit-access" }, var.tags)
  }
}
