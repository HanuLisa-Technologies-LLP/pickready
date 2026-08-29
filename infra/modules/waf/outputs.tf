output "web_acl_arn" {
  description = "Null while `enabled` is false, which is the default. A caller that needs to branch on whether the WAF exists should read `enabled`, not this."
  value       = var.enabled ? aws_wafv2_web_acl.this[0].arn : null
}

output "web_acl_name" {
  value = var.enabled ? aws_wafv2_web_acl.this[0].name : null
}

output "enforcing" {
  description = <<-EOT
    True only when the web ACL exists AND is not in count-only mode.

    Surfaced separately from `web_acl_arn` on purpose. "A WAF is attached" and
    "a WAF is refusing anything" are different facts, and reading the first as
    the second is how a control that blocks nothing ends up on a compliance
    checklist as though it did.
  EOT
  value       = var.enabled && !var.count_only
}

output "log_group_name" {
  value = var.enabled && var.enable_logging ? aws_cloudwatch_log_group.this[0].name : null
}
