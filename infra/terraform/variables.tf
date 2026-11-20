################################################################################
# Required
################################################################################

variable "entra_tenant_id" {
  description = "Microsoft Entra ID tenant ID (a GUID). Only tokens from this tenant are accepted."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$", var.entra_tenant_id))
    error_message = "entra_tenant_id must be a GUID such as 11111111-2222-3333-4444-555555555555."
  }
}

variable "entra_api_audiences" {
  description = <<-EOT
    Accepted token audiences: the client ID of the Entra ID app registration that represents this API
    (the "service app"), in both forms, for example ["<client-id>", "api://<client-id>"].
  EOT
  type        = list(string)

  validation {
    condition     = length(var.entra_api_audiences) > 0
    error_message = "Provide at least one audience."
  }
}

variable "accounts" {
  description = <<-EOT
    AWS accounts that can be requested, and who may approve requests for each.
    Owners and deputies are IAM Identity Center user names. Only they can approve or reject requests,
    and only requests for registered accounts are accepted.

    Optional. Leave empty to manage the accounts table directly in DynamoDB instead (items with
    AccountId (S), Owners (SS) and optionally Deputies (SS), names in lower case). Do not manage the
    same account ID both here and directly, or Terraform will overwrite the item.
  EOT
  type = map(object({
    owners   = list(string)
    deputies = optional(list(string), [])
  }))
  default = {}

  validation {
    condition     = alltrue([for id, _ in var.accounts : can(regex("^[0-9]{12}$", id))])
    error_message = "Account IDs must be 12 digits."
  }

  validation {
    condition     = alltrue([for _, account in var.accounts : length(account.owners) > 0])
    error_message = "Every account needs at least one owner."
  }
}

################################################################################
# Identity
################################################################################

variable "entra_required_scope" {
  description = "Delegated scope that tokens must carry. Application-only tokens have no scope and are rejected."
  type        = string
  default     = "access_as_user"
}

variable "username_claim" {
  description = "Token claim that holds the user's sign-in name."
  type        = string
  default     = "preferred_username"
}

variable "strip_username_domains" {
  description = <<-EOT
    Domains removed from sign-in names to get the IAM Identity Center user name, for example
    ["contoso.onmicrosoft.com"] turns alex@contoso.onmicrosoft.com into alex. Leave empty if your
    Identity Center user names are the full sign-in names.
  EOT
  type        = list(string)
  default     = []
}

################################################################################
# Behavior
################################################################################

variable "max_duration_hours" {
  description = "Longest access a request can ask for, in hours (1 to 72)."
  type        = number
  default     = 8

  validation {
    condition     = var.max_duration_hours >= 1 && var.max_duration_hours <= 72
    error_message = "max_duration_hours must be between 1 and 72."
  }
}

variable "approval_window_hours" {
  description = "How long a request can wait for a decision before it can no longer be approved."
  type        = number
  default     = 24
}

variable "allow_self_approval" {
  description = <<-EOT
    Let an owner or deputy approve their own request. Keep this false in production: a second person
    should approve. Set it to true to try the solution alone.
  EOT
  type        = bool
  default     = false
}

variable "allowed_managed_policy_arns" {
  description = "If not empty, requests for an AWS managed policy may only use these ARNs. Custom policies are not affected."
  type        = list(string)
  default     = []
}

variable "permission_set_session_duration" {
  description = <<-EOT
    Session duration of the temporary permission sets (ISO 8601, for example PT1H). Removing access
    does not end sessions already in progress, so keep this short.
  EOT
  type        = string
  default     = "PT1H"
}

variable "request_retention_days" {
  description = "How long request records are kept before DynamoDB deletes them."
  type        = number
  default     = 365
}

################################################################################
# AWS IAM Identity Center
################################################################################

variable "identity_center_instance_arn" {
  description = "IAM Identity Center instance ARN. Detected automatically when empty."
  type        = string
  default     = ""
}

variable "identity_center_role_arn" {
  description = "Role in the account that owns IAM Identity Center. The workflow assumes it for every Identity Center call. Leave empty when this stack runs in that account. The role must trust the jit-access-workflow role."
  type        = string
  default     = ""
}

variable "identity_store_id" {
  description = "IAM Identity Center identity store ID. Detected automatically when empty."
  type        = string
  default     = ""
}

variable "identity_center_kms_key_arn" {
  description = "If Identity Center uses a customer managed KMS key, its ARN, so the API and the workflow can read the identity store. The key policy must also allow both roles."
  type        = string
  default     = ""
}

################################################################################
# Platform
################################################################################

variable "region" {
  description = "AWS region. It must be the region of your IAM Identity Center instance."
  type        = string
  default     = "eu-central-1"
}

variable "name_prefix" {
  description = "Prefix of all resource names."
  type        = string
  default     = "jit-access"
}

variable "stage_name" {
  description = "API Gateway stage name."
  type        = string
  default     = "v1"
}

variable "authorizer_cache_seconds" {
  description = "How long API Gateway caches a token validation result (0 to 3600). 0 disables caching."
  type        = number
  default     = 0
}

variable "debug_mode" {
  description = "Debug logging in all Lambdas, and the error type and message in API 500 responses. Turn off in production."
  type        = bool
  default     = false
}

variable "api_throttle_rate" {
  description = "Steady-state requests per second for the API."
  type        = number
  default     = 10
}

variable "api_throttle_burst" {
  description = "Burst limit for the API."
  type        = number
  default     = 20
}

variable "log_retention_days" {
  description = "Retention of Lambda logs. The logs are part of the audit trail."
  type        = number
  default     = 90
}

variable "alarm_topic_arn" {
  description = "SNS topic for alarms about failed workflows. Strongly recommended: a failed workflow can leave access in place."
  type        = string
  default     = ""
}

variable "deletion_protection" {
  description = "Protect the DynamoDB tables from deletion."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Extra tags for all resources."
  type        = map(string)
  default     = {}
}
