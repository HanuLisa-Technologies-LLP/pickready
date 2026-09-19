output "bucket_name" {
  description = "The bucket raw received mail is written to."
  value       = aws_s3_bucket.mail.id
}

output "bucket_arn" {
  description = "For reference. The inbound function's read grant is built from the bucket NAME in the environment file rather than from this output: the function must exist before this module can subscribe it, so taking the grant from here would be a cycle."
  value       = aws_s3_bucket.mail.arn
}

output "topic_arn" {
  description = "The SNS topic SES publishes a received message to."
  value       = aws_sns_topic.received.arn
}

output "reply_domain" {
  description = "Echoed so an environment can pass the same value to the API's INBOUND_EMAIL_DOMAIN and the two cannot drift."
  value       = var.reply_domain
}

output "rule_set_name" {
  description = "The receipt rule set. Active only when `activate_rule_set` is true."
  value       = aws_ses_receipt_rule_set.this.rule_set_name
}
