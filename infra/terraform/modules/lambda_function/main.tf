################################################################################
# One Lambda function with its own log group and its own least-privilege role.
################################################################################

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

data "aws_iam_policy_document" "assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# Write to its own log group only, plus whatever the function needs.
data "aws_iam_policy_document" "permissions" {
  source_policy_documents = var.policy_json == "" ? [] : [var.policy_json]

  statement {
    sid       = "WriteOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.this.arn}:*"]
  }
}

resource "aws_iam_role" "this" {
  name               = var.name
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "this" {
  name   = var.name
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.permissions.json
}

resource "aws_lambda_function" "this" {
  function_name = var.name
  description   = var.description
  role          = aws_iam_role.this.arn

  runtime       = "python3.12"
  architectures = ["arm64"]
  handler       = var.handler
  timeout       = var.timeout
  memory_size   = var.memory_size

  filename         = var.filename
  source_code_hash = var.source_code_hash
  layers           = var.layers

  environment {
    variables = var.environment
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.this.name
    # Lambda drops logs below this level, so it must match the Powertools level (debug_mode).
    application_log_level = lookup(var.environment, "POWERTOOLS_LOG_LEVEL", "INFO")
  }

  tags = var.tags

  depends_on = [aws_iam_role_policy.this]
}
