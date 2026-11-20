################################################################################
# The API's OpenAPI document. AgentCore Gateway reads it from S3 and turns each
# operation into one MCP tool, so it defines exactly what the agent can do.
#
# `make build` generates it from the code and flattens it for Copilot Studio.
# Here the placeholder server URL is replaced by the real API URL.
################################################################################

resource "aws_s3_bucket" "openapi" {
  bucket = "${var.name_prefix}-openapi-${local.account_id}-${local.region}"
}

resource "aws_s3_bucket_public_access_block" "openapi" {
  bucket                  = aws_s3_bucket.openapi.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "openapi" {
  bucket = aws_s3_bucket.openapi.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "openapi" {
  bucket = aws_s3_bucket.openapi.id
  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_iam_policy_document" "openapi_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.openapi.arn, "${aws_s3_bucket.openapi.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "openapi" {
  bucket = aws_s3_bucket.openapi.id
  policy = data.aws_iam_policy_document.openapi_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.openapi]
}

locals {
  openapi_document = merge(
    jsondecode(file("${local.build_dir}/openapi.flat.json")),
    { servers = [{ url = local.api_url }] },
  )
}

resource "aws_s3_object" "openapi" {
  bucket       = aws_s3_bucket.openapi.id
  key          = "openapi.json"
  content_type = "application/json"
  content      = jsonencode(local.openapi_document)
  etag         = md5(jsonencode(local.openapi_document))
}
