################################################################################
# Lambda functions
#
# All five functions run the same code package (`make build`), each with its own
# handler, its own role, and only the permissions it needs.
################################################################################

locals {
  code_zip  = "${local.build_dir}/function.zip"
  code_hash = filebase64sha256("${local.build_dir}/function.zip")
}

# ----------------------------------------------------------------------------- API

data "aws_iam_policy_document" "api" {
  statement {
    sid     = "Requests"
    effect  = "Allow"
    actions = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [
      aws_dynamodb_table.requests.arn,
      "${aws_dynamodb_table.requests.arn}/index/*",
    ]
  }

  statement {
    sid       = "ReadAccountOwnersAndUsers"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.accounts.arn, aws_dynamodb_table.users.arn]
  }

  # Confirm that a requester exists in IAM Identity Center before the request is stored.
  statement {
    sid       = "FindUser"
    effect    = "Allow"
    actions   = ["identitystore:GetUserId"]
    resources = ["*"]
  }

  # Identity Center encrypted with a customer managed KMS key: identity store reads need Decrypt.
  dynamic "statement" {
    for_each = var.identity_center_kms_key_arn == "" ? [] : [var.identity_center_kms_key_arn]
    content {
      sid       = "DecryptIdentityStore"
      effect    = "Allow"
      actions   = ["kms:Decrypt"]
      resources = [statement.value]
    }
  }
}

module "api" {
  source = "./modules/lambda_function"

  name        = "${var.name_prefix}-api"
  description = "Just-in-time access API: request, list, get, approve, reject."
  handler     = "jit_access.api.app.lambda_handler"
  timeout     = 30

  filename           = local.code_zip
  source_code_hash   = local.code_hash
  layers             = [aws_lambda_layer_version.dependencies.arn]
  policy_json        = data.aws_iam_policy_document.api.json
  log_retention_days = var.log_retention_days

  environment = merge(local.common_environment, {
    REQUESTS_TABLE               = aws_dynamodb_table.requests.name
    ACCOUNTS_TABLE               = aws_dynamodb_table.accounts.name
    USERS_TABLE                  = aws_dynamodb_table.users.name
    IDENTITY_STORE_ID            = local.identity_store_id
    IDENTITY_CENTER_INSTANCE_ARN = local.instance_arn
    MAX_DURATION_HOURS           = tostring(var.max_duration_hours)
    APPROVAL_WINDOW_HOURS        = tostring(var.approval_window_hours)
    ALLOW_SELF_APPROVAL          = tostring(var.allow_self_approval)
    ALLOWED_MANAGED_POLICY_ARNS  = join(",", var.allowed_managed_policy_arns)
    REQUEST_RETENTION_DAYS       = tostring(var.request_retention_days)
    STRIP_USERNAME_DOMAINS       = join(",", var.strip_username_domains)
    POWERTOOLS_SERVICE_NAME      = "jit-access-api"
  })

  tags = var.tags
}

# ----------------------------------------------------------------------------- authorizer

module "authorizer" {
  source = "./modules/lambda_function"

  name        = "${var.name_prefix}-authorizer"
  description = "Validates Microsoft Entra ID access tokens for the API."
  handler     = "jit_access.authorizer.app.lambda_handler"
  timeout     = 10

  filename           = local.code_zip
  source_code_hash   = local.code_hash
  layers             = [aws_lambda_layer_version.dependencies.arn]
  log_retention_days = var.log_retention_days

  # Needs no AWS permissions: it only reads the tenant's public signing keys over the internet.
  environment = merge(local.common_environment, {
    ENTRA_TENANT_ID         = var.entra_tenant_id
    ENTRA_AUDIENCES         = join(",", var.entra_api_audiences)
    ENTRA_REQUIRED_SCOPE    = var.entra_required_scope
    USERNAME_CLAIM          = var.username_claim
    STRIP_USERNAME_DOMAINS  = join(",", var.strip_username_domains)
    POWERTOOLS_SERVICE_NAME = "jit-access-authorizer"
  })

  tags = var.tags
}

# ----------------------------------------------------------------------------- workflow tasks

data "aws_iam_policy_document" "workflow" {
  statement {
    sid       = "RequestRecord"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.requests.arn]
  }

  statement {
    sid       = "ReadUsers"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.users.arn]
  }

  # These IAM Identity Center actions do not support narrower resources in practice. The role
  # is as powerful as an Identity Center administrator, so protect it (see docs/security.md).
  statement {
    sid    = "PermissionSetsAndAssignments"
    effect = "Allow"
    actions = [
      "sso:CreatePermissionSet",
      "sso:DeletePermissionSet",
      "sso:DescribePermissionSet",
      "sso:ListPermissionSets",
      "sso:AttachManagedPolicyToPermissionSet",
      "sso:PutInlinePolicyToPermissionSet",
      "sso:ProvisionPermissionSet",
      "sso:CreateAccountAssignment",
      "sso:DeleteAccountAssignment",
      "sso:ListAccountAssignments",
      "sso:DescribeAccountAssignmentCreationStatus",
      "sso:DescribeAccountAssignmentDeletionStatus",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "FindUser"
    effect    = "Allow"
    actions   = ["identitystore:GetUserId"]
    resources = ["*"]
  }

  # Read-only. Identity Center looks up the target account in AWS Organizations.
  statement {
    sid    = "ReadOrganization"
    effect = "Allow"
    actions = [
      "organizations:DescribeAccount",
      "organizations:DescribeOrganization",
      "organizations:ListAccounts",
      "organizations:ListParents",
      "organizations:ListRoots",
    ]
    resources = ["*"]
  }

  # Identity Center in another account: the workflow works through a role there.
  dynamic "statement" {
    for_each = var.identity_center_role_arn == "" ? [] : [var.identity_center_role_arn]
    content {
      sid       = "AssumeIdentityCenterRole"
      effect    = "Allow"
      actions   = ["sts:AssumeRole"]
      resources = [statement.value]
    }
  }

  dynamic "statement" {
    for_each = var.identity_center_kms_key_arn == "" ? [] : [1]
    content {
      sid    = "IdentityCenterKey"
      effect = "Allow"
      # Delegated administrators need all four to write Identity Center data (permission sets,
      # assignments) when it is encrypted with a customer managed key. See the AWS "Baseline KMS
      # key policy" page for IAM Identity Center.
      actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKeyWithoutPlaintext", "kms:DescribeKey"]
      resources = [var.identity_center_kms_key_arn]
    }
  }
}

module "workflow" {
  source = "./modules/lambda_function"

  name        = "${var.name_prefix}-workflow"
  description = "Creates and removes the temporary access in AWS IAM Identity Center."
  handler     = "jit_access.workflow.app.lambda_handler"
  timeout     = 60

  filename           = local.code_zip
  source_code_hash   = local.code_hash
  layers             = [aws_lambda_layer_version.dependencies.arn]
  policy_json        = data.aws_iam_policy_document.workflow.json
  log_retention_days = var.log_retention_days

  environment = merge(local.common_environment, {
    REQUESTS_TABLE                  = aws_dynamodb_table.requests.name
    USERS_TABLE                     = aws_dynamodb_table.users.name
    IDENTITY_CENTER_ROLE_ARN        = var.identity_center_role_arn
    IDENTITY_STORE_ID               = local.identity_store_id
    IDENTITY_CENTER_INSTANCE_ARN    = local.instance_arn
    PERMISSION_SET_SESSION_DURATION = var.permission_set_session_duration
    POWERTOOLS_SERVICE_NAME         = "jit-access-workflow"
  })

  tags = var.tags
}

# ----------------------------------------------------------------------------- trigger

data "aws_iam_policy_document" "trigger" {
  statement {
    sid       = "StartWorkflow"
    effect    = "Allow"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.access.arn]
  }

  statement {
    sid       = "ReadRequestsStream"
    effect    = "Allow"
    actions   = ["dynamodb:DescribeStream", "dynamodb:GetRecords", "dynamodb:GetShardIterator"]
    resources = [aws_dynamodb_table.requests.stream_arn]
  }

  statement {
    sid       = "ListStreams"
    effect    = "Allow"
    actions   = ["dynamodb:ListStreams"]
    resources = ["*"]
  }
}

module "trigger" {
  source = "./modules/lambda_function"

  name        = "${var.name_prefix}-trigger"
  description = "Starts the access workflow when a request is approved."
  handler     = "jit_access.trigger.app.lambda_handler"
  timeout     = 30

  filename           = local.code_zip
  source_code_hash   = local.code_hash
  layers             = [aws_lambda_layer_version.dependencies.arn]
  policy_json        = data.aws_iam_policy_document.trigger.json
  log_retention_days = var.log_retention_days

  environment = merge(local.common_environment, {
    STATE_MACHINE_ARN       = aws_sfn_state_machine.access.arn
    POWERTOOLS_SERVICE_NAME = "jit-access-trigger"
  })

  tags = var.tags
}

# ----------------------------------------------------------------------------- gateway interceptor

module "interceptor" {
  source = "./modules/lambda_function"

  name        = "${var.name_prefix}-interceptor"
  description = "Forwards the caller's token from the AgentCore Gateway to the API."
  handler     = "jit_access.interceptor.app.lambda_handler"
  timeout     = 5

  filename           = local.code_zip
  source_code_hash   = local.code_hash
  layers             = [aws_lambda_layer_version.dependencies.arn]
  log_retention_days = var.log_retention_days

  environment = merge(local.common_environment, {
    POWERTOOLS_SERVICE_NAME = "jit-access-interceptor"
  })

  tags = var.tags
}
