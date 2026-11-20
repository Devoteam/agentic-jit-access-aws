variable "name" {
  description = "Function name. Also used for the IAM role and the log group."
  type        = string
}

variable "description" {
  description = "What the function does."
  type        = string
  default     = ""
}

variable "handler" {
  description = "Handler, for example jit_access.api.app.lambda_handler."
  type        = string
}

variable "filename" {
  description = "Path to the deployment package (zip)."
  type        = string
}

variable "source_code_hash" {
  description = "Base64 SHA-256 of the deployment package, so changes trigger an update."
  type        = string
}

variable "layers" {
  description = "Lambda layer ARNs."
  type        = list(string)
  default     = []
}

variable "environment" {
  description = "Environment variables."
  type        = map(string)
  default     = {}
}

variable "policy_json" {
  description = "IAM policy document with the permissions the function needs. Empty for none."
  type        = string
  default     = ""
}

variable "timeout" {
  description = "Timeout in seconds."
  type        = number
  default     = 30
}

variable "memory_size" {
  description = "Memory in MB."
  type        = number
  default     = 256
}

variable "log_retention_days" {
  description = "How long to keep logs. The logs are part of the audit trail."
  type        = number
  default     = 90
}

variable "tags" {
  description = "Tags."
  type        = map(string)
  default     = {}
}
