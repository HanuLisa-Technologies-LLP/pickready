/**
 * Route53 alias records pointing the product's hostnames at the load balancer
 * (spec-doc6 §13.2).
 *
 * THIS MODULE CREATES NO HOSTED ZONE, AND WILL NOT BE MADE TO
 * ------------------------------------------------------------
 * spec-doc6 §13.2: the hosted zone is a "reference (data source or variable,
 * never created blindly)". The zone id arrives as a variable with no default.
 *
 * Creating a zone here would be the single most expensive mistake this layer
 * can make. A Route53 hosted zone gets four assigned name servers at creation,
 * and they are different from the four the registrar is already delegating to.
 * So a `terraform apply` that creates a second zone for a domain that already
 * has one produces a zone full of correct records that nothing on the internet
 * resolves, while the real zone keeps serving the old answers. Everything looks
 * applied. Nothing changed. And the fix is a registrar change with its own
 * propagation delay.
 *
 * A name-based `data "aws_route53_zone"` lookup has a quieter version of the
 * same failure: it resolves to whichever zone matches the name, including a
 * stale duplicate, and it needs a live API call the offline plan in §13.3
 * cannot make. So the id is passed in, and it is unambiguous.
 *
 * ALIAS RECORDS RATHER THAN CNAMEs
 * ---------------------------------
 * A CNAME cannot exist at a zone apex, which rules it out for the bare domain
 * before any other consideration. An alias also costs nothing to resolve, and
 * it tracks the load balancer's addresses as they change, which they do:
 * an ALB's IPs are not stable and a hardcoded A record would be correct until
 * the first scaling event.
 *
 * `evaluate_target_health = false` IS DELIBERATE. With it true, Route53 stops
 * returning the record when the load balancer's targets are unhealthy, and the
 * visitor's browser reports NXDOMAIN. That is strictly worse than reaching the
 * ALB and getting its 503: a DNS failure is cached by resolvers past the
 * recovery, and it is indistinguishable from the domain having been taken away.
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

# IPv4.
resource "aws_route53_record" "a" {
  for_each = toset(var.hostnames)

  zone_id = var.hosted_zone_id
  name    = each.value
  type    = "A"

  alias {
    name                   = var.alb_dns_name
    zone_id                = var.alb_zone_id
    evaluate_target_health = false
  }
}

# IPv6, from the same alias target.
#
# The ALB is dualstack-capable and a client on an IPv6-only mobile network needs
# the AAAA to reach it at all. The failure without this record is not a slow
# page: it is a candidate on a mobile carrier who cannot open the job link, and
# it is invisible from any office network.
resource "aws_route53_record" "aaaa" {
  for_each = var.create_ipv6_records ? toset(var.hostnames) : toset([])

  zone_id = var.hosted_zone_id
  name    = each.value
  type    = "AAAA"

  alias {
    name                   = var.alb_dns_name
    zone_id                = var.alb_zone_id
    evaluate_target_health = false
  }
}
