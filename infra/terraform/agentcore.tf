################################################################################
# Amazon Bedrock AgentCore Gateway
#
# The agent in Microsoft Copilot Studio calls this gateway over the Model Context
# Protocol (MCP). The gateway:
#
#   1. accepts only tokens issued by your Entra ID tenant for your API,
#   2. turns the API's OpenAPI document into five MCP tools,
#   3. calls the API, forwarding the caller's own token through the interceptor.
#
# The gateway target (the link between the gateway and the OpenAPI document) is
# created by scripts/publish_gateway_target.sh, because the Terraform resource for
# gateway targets is not reliable yet.
################################################################################

data "aws_iam_policy_document" "gateway_assume" {
  statement {
    sid     = "GatewayAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:gateway/*"]
    }
  }
}

resource "aws_iam_role" "gateway" {
  name               = "${var.name_prefix}-gateway"
  assume_role_policy = data.aws_iam_policy_document.gateway_assume.json
}

data "aws_iam_policy_document" "gateway" {
  statement {
    sid       = "InvokeInterceptor"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [module.interceptor.arn]
  }

  statement {
    sid       = "ReadApiDefinition"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.openapi.arn}/${aws_s3_object.openapi.key}"]
  }
}

resource "aws_iam_role_policy" "gateway" {
  name   = "invoke-interceptor-and-read-api-definition"
  role   = aws_iam_role.gateway.id
  policy = data.aws_iam_policy_document.gateway.json
}

resource "aws_bedrockagentcore_gateway" "this" {
  name            = "${var.name_prefix}-gateway"
  description     = "Just-in-time access tools for the Microsoft Copilot Studio agent (Entra ID authentication)."
  role_arn        = aws_iam_role.gateway.arn
  protocol_type   = "MCP"
  authorizer_type = "CUSTOM_JWT"

  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url    = "https://login.microsoftonline.com/${var.entra_tenant_id}/v2.0/.well-known/openid-configuration"
      allowed_audience = var.entra_api_audiences
    }
  }

  protocol_configuration {
    mcp {
      instructions = "Tools for requesting, approving and rejecting just-in-time access to AWS accounts."
      # Copilot Studio may negotiate any of these protocol versions. AgentCore does not allow adding
      # versions to an existing gateway, so a change here replaces the gateway (new ID and URL).
      supported_versions = ["2025-03-26", "2025-06-18", "2025-11-25"]
    }
  }

  interceptor_configuration {
    interception_points = ["REQUEST"]

    interceptor {
      lambda {
        arn = module.interceptor.arn
      }
    }

    input_configuration {
      pass_request_headers = true
    }
  }

  depends_on = [aws_iam_role_policy.gateway]
}
