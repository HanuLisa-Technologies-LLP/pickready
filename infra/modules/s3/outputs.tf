output "bucket_name" {
  description = "Set as S3_BUCKET on every task that stores a file."
  value       = aws_s3_bucket.private.id
}

output "bucket_arn" {
  value = aws_s3_bucket.private.arn
}

output "access_policy_arn" {
  description = "Attached to the task roles that store files. Scoped to the application prefixes, never to the bucket."
  value       = aws_iam_policy.application.arn
}
