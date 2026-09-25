output "project_name" {
  description = "What scripts/build-images-remote.sh starts. The script discovers the bucket and the registries from the project itself, so it never needs Terraform state."
  value       = aws_codebuild_project.this.name
}

output "source_bucket" {
  value = aws_s3_bucket.source.id
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.build.name
}

output "role_arn" {
  value = aws_iam_role.build.arn
}
