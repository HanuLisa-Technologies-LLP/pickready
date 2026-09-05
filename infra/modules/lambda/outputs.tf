output "function_names" {
  description = "{key -> the deployed function name}. What CI passes to `aws lambda update-function-code`."
  value       = { for name, fn in aws_lambda_function.this : name => fn.function_name }
}

output "function_arns" {
  value = { for name, fn in aws_lambda_function.this : name => fn.arn }
}

output "execution_role_arns" {
  description = "One per function. See the module docstring for why they are not shared."
  value       = { for name, role in aws_iam_role.this : name => role.arn }
}

output "log_group_names" {
  value = { for name, group in aws_cloudwatch_log_group.this : name => group.name }
}
