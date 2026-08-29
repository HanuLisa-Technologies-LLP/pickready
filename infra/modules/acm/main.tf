/**
 * ACM certificate with DNS validation, and no manual step (spec-doc6 §13.2).
 *
 * WHY DNS VALIDATION AND NOT EMAIL
 * ---------------------------------
 * Email validation sends to five fixed addresses at the domain and to the WHOIS
 * contact, and a person has to click a link within 72 hours. It cannot be
 * automated, it cannot be re-run by a pipeline, and every renewal repeats it.
 * DNS validation writes one CNAME per name and then renews itself for as long
 * as the record exists. That is the difference between a certificate the
 * infrastructure owns and one that depends on somebody reading an inbox.
 *
 * THE VALIDATION RECORDS ARE CREATED HERE, IN THIS MODULE, ON PURPOSE
 * --------------------------------------------------------------------
 * It would be tidier to put every Route53 record in `modules/dns`. It would
 * also be wrong: `aws_acm_certificate_validation` blocks until the records
 * resolve, so splitting the certificate from its own validation records puts a
 * wait on one side of a module boundary and the thing it waits for on the
 * other. When that ordering slips the failure is a fifteen-minute hang followed
 * by a timeout, which reads like a network problem rather than like a
 * dependency problem.
 *
 * So this module owns the certificate and the records that prove it. The `dns`
 * module owns the records that ROUTE traffic. The split is by purpose rather
 * than by resource type, and each side is a closed loop.
 *
 * `create_before_destroy` ON THE CERTIFICATE IS LOAD-BEARING. A certificate in
 * use by a listener cannot be deleted, so a change that replaces it (adding a
 * subject alternative name, for instance) deadlocks without it: Terraform tries
 * to destroy the old certificate first and AWS refuses because the listener
 * still references it.
 */

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60"
    }
  }
}

resource "aws_acm_certificate" "this" {
  domain_name               = var.domain_name
  subject_alternative_names = var.subject_alternative_names
  validation_method         = "DNS"

  # RSA 2048 rather than an EC key. Every current browser handles both; the
  # difference that matters here is that some older corporate TLS-inspecting
  # middleboxes, which a recruiter's employer may well run, still do not
  # negotiate EC certificates, and this product's audience is behind exactly
  # that kind of network.
  key_algorithm = "RSA_2048"

  options {
    # Certificate Transparency is on. It is on by default and is worth stating:
    # the alternative logs nothing, and a certificate nobody can see issued for
    # your domain is one nobody can notice being issued for your domain.
    certificate_transparency_logging_preference = "ENABLED"
  }

  tags = merge(var.tags, { Name = "${var.project}-${var.environment}" })

  lifecycle {
    create_before_destroy = true
  }
}

# ONE VALIDATION RECORD PER DISTINCT NAME.
#
# `domain_validation_options` produces one entry per name on the certificate,
# and a wildcard shares its validation record with the apex it wildcards, so the
# set is deduplicated by record name here. Without the dedup, two entries write
# the same record and Terraform reports a duplicate-key error at plan time.
resource "aws_route53_record" "validation" {
  for_each = {
    for option in aws_acm_certificate.this.domain_validation_options :
    option.domain_name => {
      name  = option.resource_record_name
      type  = option.resource_record_type
      value = option.resource_record_value
    }
  }

  zone_id = var.hosted_zone_id
  name    = each.value.name
  type    = each.value.type
  records = [each.value.value]
  ttl     = 60

  # ALLOW_OVERWRITE. A re-run that produces the same validation record must not
  # fail because the record already exists: the alternative is a certificate
  # that can never be re-created without somebody deleting a DNS record by hand,
  # which is precisely the manual step this module exists to remove.
  allow_overwrite = true
}

# THE THING THAT MAKES IT "NO MANUAL STEPS".
#
# This resource creates nothing. It blocks until ACM has seen the records above
# and moved the certificate to ISSUED, so the HTTPS listener that consumes
# `certificate_arn` cannot be created against a certificate still in
# PENDING_VALIDATION -- which AWS accepts and then serves as a TLS handshake
# failure to every visitor.
resource "aws_acm_certificate_validation" "this" {
  certificate_arn         = aws_acm_certificate.this.arn
  validation_record_fqdns = [for record in aws_route53_record.validation : record.fqdn]

  timeouts {
    # DNS validation is usually minutes. When it is not, the cause is nearly
    # always that the hosted zone passed in is not the zone the domain's
    # registrar delegates to, and no amount of further waiting fixes that.
    create = var.validation_timeout
  }
}
