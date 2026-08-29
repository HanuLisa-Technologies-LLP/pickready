output "arn" {
  description = "The load balancer's ARN. `modules/waf` associates its web ACL with this."
  value       = aws_lb.this.arn
}

output "dns_name" {
  description = "The load balancer's own hostname. `modules/dns` aliases the product domain to it rather than CNAMEing, because an alias resolves at the zone apex and a CNAME cannot."
  value       = aws_lb.this.dns_name
}

output "zone_id" {
  description = "The load balancer's hosted zone, needed by the Route53 alias record. This is the ALB's zone, not the product domain's."
  value       = aws_lb.this.zone_id
}

output "target_group_arns" {
  description = "{key -> ARN}. Passed straight into the ECS module's `services[*].target_group_arn`, which is what registers the tasks."
  value       = { for key, group in aws_lb_target_group.this : key => group.arn }
}

output "https_listener_arn" {
  value = aws_lb_listener.https.arn
}

output "access_logs_bucket" {
  value = aws_s3_bucket.logs.id
}

output "public_listener_rules" {
  description = <<-EOT
    {path pattern -> the listener rule ARN serving it without authentication}.

    Surfaced deliberately, the same way `modules/secrets` surfaces its
    per-service policies. spec-doc6 §13.2 asks that the unauthenticated public
    job path be asserted "at the ALB/listener-rule level", and this makes the
    answer readable from `terraform output` rather than by reading the rule set.
    An unexpected entry here is a widened public surface.
  EOT
  value       = { for pattern, rule in aws_lb_listener_rule.public : pattern => rule.arn }
}
