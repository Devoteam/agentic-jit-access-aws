output "arn" {
  description = "Function ARN."
  value       = aws_lambda_function.this.arn
}

output "name" {
  description = "Function name."
  value       = aws_lambda_function.this.function_name
}

output "invoke_arn" {
  description = "ARN API Gateway uses to invoke the function."
  value       = aws_lambda_function.this.invoke_arn
}

output "role_name" {
  description = "Name of the execution role."
  value       = aws_iam_role.this.name
}

output "environment" {
  description = "Environment variables of the function. None of them is a secret."
  value       = var.environment
}
