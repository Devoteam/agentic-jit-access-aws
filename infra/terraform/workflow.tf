################################################################################
# Access workflow (AWS Step Functions)
#
#   Provision -> wait -> check -> Activate -> wait for the approved duration
#     -> Revoke -> wait -> check -> Finalize
#
# Any failure before the access ends goes to Revoke first, so a failed run never
# leaves a permission set or an assignment behind. The state machine only needs
# the request ID: everything else is read from, and saved to, the request record.
################################################################################

locals {
  workflow_arn = module.workflow.arn

  lambda_retry = [
    {
      ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"]
      IntervalSeconds = 2
      MaxAttempts     = 6
      BackoffRate     = 2
    },
    {
      # Raised by the workflow Lambda for conditions that clear up on their own.
      ErrorEquals     = ["RetryLater"]
      IntervalSeconds = 10
      MaxAttempts     = 12
      BackoffRate     = 1.5
    },
  ]

  # Any error while granting goes to Revoke, with the error saved for Finalize.
  catch_to_revoke = [
    {
      ErrorEquals = ["States.ALL"]
      ResultPath  = "$.Error"
      Next        = "Revoke"
    },
  ]
}

resource "aws_sfn_state_machine" "access" {
  name     = "${var.name_prefix}-access"
  role_arn = aws_iam_role.state_machine.arn

  definition = jsonencode({
    Comment = "Grant just-in-time access, wait for the approved duration, then remove it."
    StartAt = "Provision"
    States = {
      Provision = {
        Type           = "Task"
        Resource       = local.workflow_arn
        Parameters     = { "Action" = "provision", "RequestId.$" = "$.RequestId" }
        ResultPath     = "$.Provision"
        TimeoutSeconds = 120
        Retry          = local.lambda_retry
        Catch          = local.catch_to_revoke
        Next           = "WaitProvision"
      }
      WaitProvision = {
        Type    = "Wait"
        Seconds = 5
        Next    = "CheckProvision"
      }
      CheckProvision = {
        Type           = "Task"
        Resource       = local.workflow_arn
        Parameters     = { "Action" = "check_provision", "RequestId.$" = "$.RequestId" }
        ResultPath     = "$.Provision"
        TimeoutSeconds = 120
        Retry          = local.lambda_retry
        Catch          = local.catch_to_revoke
        Next           = "IsProvisioned"
      }
      IsProvisioned = {
        Type = "Choice"
        Choices = [
          { Variable = "$.Provision.Status", StringEquals = "SUCCEEDED", Next = "Activate" },
          { Variable = "$.Provision.Status", StringEquals = "FAILED", Next = "ProvisioningFailed" },
        ]
        Default = "WaitProvision"
      }
      ProvisioningFailed = {
        Type       = "Pass"
        Parameters = { "Error" = "ProvisioningFailed", "Cause.$" = "$.Provision.Reason" }
        ResultPath = "$.Error"
        Next       = "Revoke"
      }
      Activate = {
        Type           = "Task"
        Resource       = local.workflow_arn
        Parameters     = { "Action" = "activate", "RequestId.$" = "$.RequestId" }
        ResultPath     = "$.Activation"
        TimeoutSeconds = 120
        Retry          = local.lambda_retry
        Catch          = local.catch_to_revoke
        Next           = "WaitForAccessToEnd"
      }
      WaitForAccessToEnd = {
        Type        = "Wait"
        SecondsPath = "$.Activation.WaitSeconds"
        Next        = "Revoke"
      }
      Revoke = {
        Type           = "Task"
        Resource       = local.workflow_arn
        Parameters     = { "Action" = "revoke", "RequestId.$" = "$.RequestId" }
        ResultPath     = "$.Revoke"
        TimeoutSeconds = 120
        Retry          = local.lambda_retry
        Next           = "WaitRevoke"
      }
      WaitRevoke = {
        Type    = "Wait"
        Seconds = 5
        Next    = "CheckRevoke"
      }
      CheckRevoke = {
        Type           = "Task"
        Resource       = local.workflow_arn
        Parameters     = { "Action" = "check_revoke", "RequestId.$" = "$.RequestId" }
        ResultPath     = "$.Revoke"
        TimeoutSeconds = 120
        Retry          = local.lambda_retry
        Next           = "IsRevoked"
      }
      IsRevoked = {
        Type = "Choice"
        Choices = [
          { Variable = "$.Revoke.Status", StringEquals = "DONE", Next = "HadError" },
        ]
        Default = "WaitRevoke"
      }
      HadError = {
        Type = "Choice"
        Choices = [
          { Variable = "$.Error", IsPresent = true, Next = "FinalizeFailed" },
        ]
        Default = "FinalizeExpired"
      }
      FinalizeFailed = {
        Type           = "Task"
        Resource       = local.workflow_arn
        Parameters     = { "Action" = "finalize", "RequestId.$" = "$.RequestId", "Outcome" = "Failed", "Reason.$" = "$.Error.Cause" }
        ResultPath     = "$.Finalize"
        TimeoutSeconds = 120
        Retry          = local.lambda_retry
        Next           = "AccessFailed"
      }
      AccessFailed = {
        Type  = "Fail"
        Error = "AccessProvisioningFailed"
        Cause = "The access could not be granted. Everything that was created has been removed. See the request record."
      }
      FinalizeExpired = {
        Type           = "Task"
        Resource       = local.workflow_arn
        Parameters     = { "Action" = "finalize", "RequestId.$" = "$.RequestId", "Outcome" = "Expired" }
        ResultPath     = "$.Finalize"
        TimeoutSeconds = 120
        Retry          = local.lambda_retry
        End            = true
      }
    }
  })
}

data "aws_iam_policy_document" "state_machine_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "state_machine" {
  name               = "${var.name_prefix}-access-workflow"
  assume_role_policy = data.aws_iam_policy_document.state_machine_assume.json
}

data "aws_iam_policy_document" "state_machine" {
  statement {
    sid       = "InvokeWorkflowTasks"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [module.workflow.arn, "${module.workflow.arn}:*"]
  }
}

resource "aws_iam_role_policy" "state_machine" {
  name   = "invoke-workflow-tasks"
  role   = aws_iam_role.state_machine.id
  policy = data.aws_iam_policy_document.state_machine.json
}

################################################################################
# Approval -> workflow. The stream of the requests table starts the workflow when
# a request moves from Pending to Approved. Failed records are retried by the stream.
################################################################################

resource "aws_lambda_event_source_mapping" "approvals" {
  event_source_arn  = aws_dynamodb_table.requests.stream_arn
  function_name     = module.trigger.arn
  starting_position = "LATEST"
  batch_size        = 10

  function_response_types        = ["ReportBatchItemFailures"]
  bisect_batch_on_function_error = true

  filter_criteria {
    filter {
      pattern = jsonencode({
        eventName = ["MODIFY"]
        dynamodb = {
          OldImage = { Status = { S = ["Pending"] } }
          NewImage = { Status = { S = ["Approved"] } }
        }
      })
    }
  }
}

################################################################################
# Alarms. A workflow that fails to remove access must not go unnoticed.
################################################################################

resource "aws_cloudwatch_metric_alarm" "workflow_failed" {
  count = var.alarm_topic_arn == "" ? 0 : 1

  alarm_name          = "${var.name_prefix}-workflow-failed"
  alarm_description   = "An access workflow failed. Access may not have been removed. Check the request record and the state machine execution."
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.access.arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "trigger_errors" {
  count = var.alarm_topic_arn == "" ? 0 : 1

  alarm_name          = "${var.name_prefix}-trigger-errors"
  alarm_description   = "Approved requests could not start their workflow."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = module.trigger.name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
}
