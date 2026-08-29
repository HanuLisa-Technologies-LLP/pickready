output "certificate_arn" {
  description = <<-EOT
    The ARN of the VALIDATED certificate, taken from the validation resource
    rather than from the certificate itself.

    That is the whole point, and it is easy to get wrong. Reading
    `aws_acm_certificate.this.arn` returns an ARN as soon as the request exists,
    which lets the HTTPS listener be created against a certificate still in
    PENDING_VALIDATION. AWS accepts that listener and then fails the TLS
    handshake for every visitor. Reading it from the validation resource makes
    the listener wait for ISSUED.
  EOT
  value       = aws_acm_certificate_validation.this.certificate_arn
}

output "domain_name" {
  value = aws_acm_certificate.this.domain_name
}

output "validation_record_fqdns" {
  description = "What was written into the hosted zone to prove control of the domain. Useful when a validation hangs: compare these against what the zone actually serves."
  value       = [for record in aws_route53_record.validation : record.fqdn]
}
