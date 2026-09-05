variable "project" {
  type    = string
  default = "readypick"
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["pilot", "staging", "production"], var.environment)
    error_message = "environment must be pilot, staging or production."
  }
}

variable "domain_name" {
  description = <<-EOT
    The certificate's primary name, for example the hostname the product is
    served on.

    NO DEFAULT. spec-doc6 §D5: no domain name is available in this phase and one
    must not be asked for again, so it is a declared variable and nothing here
    invents one. A guessed domain would produce a certificate request against
    somebody else's zone.
  EOT
  type        = string

  validation {
    condition     = can(regex("^(\\*\\.)?([a-z0-9]([a-z0-9-]*[a-z0-9])?\\.)+[a-z]{2,}$", var.domain_name))
    error_message = "domain_name must be a lowercase fully qualified domain name."
  }
}

variable "subject_alternative_names" {
  description = "Additional names on the same certificate. A wildcard shares the apex's validation record, which the module deduplicates."
  type        = list(string)
  default     = []
}

variable "hosted_zone_id" {
  description = <<-EOT
    The Route53 zone the validation CNAMEs are written into.

    A VARIABLE, NOT A `data "aws_route53_zone"` LOOKUP, and that is a decision
    with two reasons behind it:

      1. spec-doc6 §13.3 requires a `terraform plan` that runs with no
         credentials. A zone lookup is a live API call and would fail there.
      2. spec-doc6 §13.2 says the hosted zone is referenced, "never created
         blindly". A name-based lookup silently resolves to whichever zone
         happens to match, including a stale duplicate zone for the same domain,
         which is one of the more expensive DNS mistakes available. An id is
         unambiguous.
  EOT
  type        = string

  validation {
    condition     = can(regex("^Z[A-Z0-9]+$", var.hosted_zone_id))
    error_message = "hosted_zone_id must be a Route53 zone id, which begins with Z."
  }
}

variable "validation_timeout" {
  description = "How long to wait for ACM to see the validation records. Past a few minutes the cause is almost always a zone that the registrar does not delegate to, and waiting longer does not fix that."
  type        = string
  default     = "20m"
}

variable "tags" {
  type    = map(string)
  default = {}
}
