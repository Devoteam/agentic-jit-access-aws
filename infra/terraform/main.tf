data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_ssoadmin_instances" "this" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  # IAM Identity Center. Detected automatically unless you set it.
  detected_instance_arn = try(tolist(data.aws_ssoadmin_instances.this.arns)[0], "")
  detected_store_id     = try(tolist(data.aws_ssoadmin_instances.this.identity_store_ids)[0], "")
  instance_arn          = var.identity_center_instance_arn != "" ? var.identity_center_instance_arn : local.detected_instance_arn
  identity_store_id     = var.identity_store_id != "" ? var.identity_store_id : local.detected_store_id

  # Build output of `make build`.
  build_dir = "${path.module}/../../build"

  common_environment = {
    POWERTOOLS_LOG_LEVEL = var.debug_mode ? "DEBUG" : "INFO"
    JIT_DEBUG            = tostring(var.debug_mode)
  }
}

# Fail early, with a clear message, if this account cannot manage IAM Identity Center.
resource "terraform_data" "preconditions" {
  lifecycle {
    precondition {
      condition     = local.instance_arn != "" && local.identity_store_id != ""
      error_message = "No IAM Identity Center instance was found in this account and region. Deploy to the account that administers IAM Identity Center (the management account or the delegated administrator), in the same region, or set identity_center_instance_arn and identity_store_id."
    }
  }
}

################################################################################
# Code and dependencies, built by `make build`
################################################################################

resource "aws_lambda_layer_version" "dependencies" {
  layer_name               = "${var.name_prefix}-dependencies"
  description              = "Powertools for AWS Lambda, Pydantic, PyJWT"
  filename                 = "${local.build_dir}/layer.zip"
  source_code_hash         = filebase64sha256("${local.build_dir}/layer.zip")
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["arm64"]
}
