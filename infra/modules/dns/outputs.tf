output "fqdns" {
  description = "The names now aliased to the load balancer. Compare against `dig` output when a cutover looks like it has not taken."
  value       = [for record in aws_route53_record.a : record.fqdn]
}

output "record_names" {
  description = "{hostname -> the record's own name as Route53 stored it}. They differ when a trailing dot or a zone suffix was implied."
  value       = { for name, record in aws_route53_record.a : name => record.name }
}
