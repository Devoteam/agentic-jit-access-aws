# Plans the whole configuration against a mocked AWS provider, so it needs no
# credentials. Run `make build` first: the plan reads the build output.
#
#   cd infra/terraform && tofu test        (or: terraform test)

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "111122223333"
    }
  }

  mock_data "aws_region" {
    defaults = {
      name   = "eu-central-1"
      region = "eu-central-1"
    }
  }

  mock_data "aws_ssoadmin_instances" {
    defaults = {
      arns               = ["arn:aws:sso:::instance/ssoins-1234567890abcdef"]
      identity_store_ids = ["d-1234567890"]
    }
  }

  # Policy documents are computed from their statements. The mock returns valid JSON.
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  # The provider validates ARNs, so computed ARNs need a realistic shape.
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::111122223333:role/mock"
    }
  }

  mock_resource "aws_lambda_layer_version" {
    defaults = {
      arn = "arn:aws:lambda:eu-central-1:111122223333:layer:mock:1"
    }
  }

  mock_resource "aws_lambda_function" {
    defaults = {
      arn        = "arn:aws:lambda:eu-central-1:111122223333:function:mock"
      invoke_arn = "arn:aws:apigateway:eu-central-1:lambda:path/2015-03-31/functions/arn:aws:lambda:eu-central-1:111122223333:function:mock/invocations"
    }
  }

  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:eu-central-1:111122223333:log-group:/aws/lambda/mock"
    }
  }

  mock_resource "aws_dynamodb_table" {
    defaults = {
      arn        = "arn:aws:dynamodb:eu-central-1:111122223333:table/mock"
      stream_arn = "arn:aws:dynamodb:eu-central-1:111122223333:table/mock/stream/2025-01-01T00:00:00.000"
    }
  }

  mock_resource "aws_sfn_state_machine" {
    defaults = {
      arn = "arn:aws:states:eu-central-1:111122223333:stateMachine:mock"
    }
  }

  mock_resource "aws_s3_bucket" {
    defaults = {
      arn = "arn:aws:s3:::mock-bucket"
    }
  }

  mock_resource "aws_api_gateway_rest_api" {
    defaults = {
      id            = "abc123"
      execution_arn = "arn:aws:execute-api:eu-central-1:111122223333:abc123"
    }
  }
}

variables {
  entra_tenant_id = "11111111-2222-3333-4444-555555555555"
  entra_api_audiences = [
    "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "api://aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  ]
  strip_username_domains = ["contoso.onmicrosoft.com"]
  accounts = {
    "111122223333" = {
      owners   = ["Alex", "alex"]
      deputies = ["sam"]
    }
    "444455556666" = {
      owners = ["carol"]
    }
  }
}

run "the_example_configuration_plans" {
  command = plan

  assert {
    condition     = length(aws_dynamodb_table_item.account) == 2
    error_message = "Every registered account needs an ownership record."
  }

  assert {
    condition     = jsondecode(aws_dynamodb_table_item.account["111122223333"].item).Owners.SS == ["alex"]
    error_message = "Owner names must be lower case and without duplicates."
  }

  assert {
    condition     = jsondecode(aws_dynamodb_table_item.account["111122223333"].item).Deputies.SS == ["sam"]
    error_message = "Deputies must be stored."
  }

  assert {
    condition     = !contains(keys(jsondecode(aws_dynamodb_table_item.account["444455556666"].item)), "Deputies")
    error_message = "An account without deputies must not get an empty set, which DynamoDB rejects."
  }
}

run "the_safe_defaults_are_on" {
  command = plan

  assert {
    condition     = module.api.environment["ALLOW_SELF_APPROVAL"] == "false"
    error_message = "Self-approval must be off by default."
  }

  assert {
    condition     = module.api.environment["MAX_DURATION_HOURS"] == "8"
    error_message = "The default maximum duration is 8 hours."
  }

  assert {
    condition     = module.workflow.environment["PERMISSION_SET_SESSION_DURATION"] == "PT1H"
    error_message = "Sessions must be short by default."
  }

  assert {
    condition     = aws_dynamodb_table.requests.point_in_time_recovery[0].enabled && aws_dynamodb_table.requests.stream_enabled
    error_message = "The requests table needs point-in-time recovery and a stream."
  }
}

run "identity_center_is_detected_automatically" {
  command = plan

  assert {
    condition     = module.workflow.environment["IDENTITY_STORE_ID"] == "d-1234567890"
    error_message = "The identity store ID must come from the detected instance."
  }
}

run "only_your_tenant_and_audience_reach_the_gateway_and_the_authorizer" {
  command = plan

  assert {
    condition     = aws_bedrockagentcore_gateway.this.authorizer_configuration[0].custom_jwt_authorizer[0].discovery_url == "https://login.microsoftonline.com/11111111-2222-3333-4444-555555555555/v2.0/.well-known/openid-configuration"
    error_message = "The gateway must trust only your tenant."
  }

  assert {
    condition     = module.authorizer.environment["ENTRA_TENANT_ID"] == "11111111-2222-3333-4444-555555555555"
    error_message = "The authorizer must validate the same tenant."
  }

  assert {
    condition     = module.authorizer.environment["ENTRA_AUDIENCES"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee,api://aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    error_message = "The authorizer must validate the same audiences as the gateway."
  }
}

run "alarms_are_only_created_when_a_topic_is_given" {
  command = plan

  assert {
    condition     = length(aws_cloudwatch_metric_alarm.workflow_failed) == 0
    error_message = "No alarm without a topic."
  }
}

run "alarms_are_created_with_a_topic" {
  command = plan

  variables {
    alarm_topic_arn = "arn:aws:sns:eu-central-1:111122223333:security-alerts"
  }

  assert {
    condition     = length(aws_cloudwatch_metric_alarm.workflow_failed) == 1 && length(aws_cloudwatch_metric_alarm.trigger_errors) == 1
    error_message = "Both alarms are expected."
  }
}

# --------------------------------------------------------------------------- invalid input

run "a_tenant_id_that_is_not_a_guid_is_rejected" {
  command = plan

  variables {
    entra_tenant_id = "contoso.onmicrosoft.com"
  }

  expect_failures = [var.entra_tenant_id]
}

run "an_account_id_that_is_not_12_digits_is_rejected" {
  command = plan

  variables {
    accounts = {
      "123" = { owners = ["alex"] }
    }
  }

  expect_failures = [var.accounts]
}

run "an_account_without_an_owner_is_rejected" {
  command = plan

  variables {
    accounts = {
      "111122223333" = { owners = [] }
    }
  }

  expect_failures = [var.accounts]
}

run "a_duration_above_72_hours_is_rejected" {
  command = plan

  variables {
    max_duration_hours = 100
  }

  expect_failures = [var.max_duration_hours]
}

run "no_audience_is_rejected" {
  command = plan

  variables {
    entra_api_audiences = []
  }

  expect_failures = [var.entra_api_audiences]
}

# --------------------------------------------------------------------------- workflow structure

run "every_state_of_the_workflow_leads_to_a_state_that_exists" {
  command = plan

  assert {
    condition = alltrue([
      for target in flatten([
        for state in values(jsondecode(aws_sfn_state_machine.access.definition).States) : concat(
          try([state.Next], []),
          try([state.Default], []),
          try([for choice in state.Choices : choice.Next], []),
          try([for handler in state.Catch : handler.Next], []),
        )
      ]) : contains(keys(jsondecode(aws_sfn_state_machine.access.definition).States), target)
    ])
    error_message = "A Next, Default or Catch target does not exist."
  }

  assert {
    condition     = contains(keys(jsondecode(aws_sfn_state_machine.access.definition).States), jsondecode(aws_sfn_state_machine.access.definition).StartAt)
    error_message = "StartAt must be an existing state."
  }
}

run "every_failure_while_granting_goes_through_revoke_first" {
  command = plan

  # Provision, check, activate: a failure must clean up before the workflow ends.
  assert {
    condition = alltrue([
      for name in ["Provision", "CheckProvision", "Activate"] :
      jsondecode(aws_sfn_state_machine.access.definition).States[name].Catch[0].Next == "Revoke"
    ])
    error_message = "A failure while granting access must run Revoke before the workflow ends."
  }

  assert {
    condition     = jsondecode(aws_sfn_state_machine.access.definition).States["ProvisioningFailed"].Next == "Revoke"
    error_message = "A failed assignment must also go through Revoke."
  }

  assert {
    condition     = jsondecode(aws_sfn_state_machine.access.definition).States["WaitForAccessToEnd"].Next == "Revoke"
    error_message = "Access must be revoked when the approved duration ends."
  }
}

# --------------------------------------------------------------------------- wrong account

run "an_account_without_identity_center_is_explained_clearly" {
  command = plan

  override_data {
    target = data.aws_ssoadmin_instances.this
    values = {
      arns               = []
      identity_store_ids = []
    }
  }

  expect_failures = [terraform_data.preconditions]
}
