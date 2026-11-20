output "gateway_mcp_url" {
  description = "MCP endpoint of the AgentCore Gateway. Use it as the server URL in Copilot Studio."
  value       = aws_bedrockagentcore_gateway.this.gateway_url
}

output "gateway_id" {
  description = "AgentCore Gateway ID. Used by scripts/publish_gateway_target.sh."
  value       = aws_bedrockagentcore_gateway.this.gateway_id
}

output "openapi_s3_uri" {
  description = "S3 location of the OpenAPI document the gateway target reads."
  value       = "s3://${aws_s3_bucket.openapi.bucket}/${aws_s3_object.openapi.key}"
}

output "api_url" {
  description = "Base URL of the API. Users never call it directly: the gateway does."
  value       = local.api_url
}

output "state_machine_arn" {
  description = "The access workflow."
  value       = aws_sfn_state_machine.access.arn
}

output "requests_table" {
  description = "DynamoDB table with every request and its audit trail."
  value       = aws_dynamodb_table.requests.name
}

output "users_table" {
  description = "DynamoDB table that maps Entra ID user names to IAM Identity Center users."
  value       = aws_dynamodb_table.users.name
}

output "accounts_table" {
  description = "DynamoDB table with the owners of each account."
  value       = aws_dynamodb_table.accounts.name
}

output "next_steps" {
  description = "What to do after terraform apply."
  value       = <<-EOT
    1. Create the gateway target:   make publish
    2. In Copilot Studio, add an MCP tool with this server URL:
         ${aws_bedrockagentcore_gateway.this.gateway_url}
       See docs/copilot-studio.md.
  EOT
}
