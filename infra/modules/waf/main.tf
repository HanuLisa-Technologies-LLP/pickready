/**
 * AWS WAF v2 web ACL in front of the Application Load Balancer
 * (spec-doc6 §13.2).
 *
 * BUILT, AND OFF. `var.enabled` defaults to false, so this module produces
 * nothing until somebody sets one variable. That is what the specification asks
 * for, and it is the right default rather than a hedge:
 *
 *   A managed rule group blocks real traffic on the day it is switched on, and
 *   the traffic it blocks is not random. `AWSManagedRulesCommonRuleSet`
 *   inspects the request body, and this product's request bodies are resumes,
 *   job descriptions written by clients, and interview answers typed by
 *   candidates. A resume containing an XML sample, a JD containing a SQL
 *   snippet, an answer describing a shell command: each is an ordinary document
 *   in this domain and each trips a rule written for a form field.
 *
 *   So enabling this is a decision that comes with a week of reading COUNT-mode
 *   metrics, not a checkbox. `var.count_only` exists to make that week
 *   possible: every rule evaluates and logs and blocks nothing, so the false
 *   positives are measurable BEFORE they are load-bearing. The module docstring
 *   is the wrong place to relitigate it and `docs/DEPLOY_AWS.md` carries the
 *   ordered procedure.
 *
 * WHAT IS DELIBERATELY EXCLUDED FROM THE MANAGED SET
 * ---------------------------------------------------
 * `SizeRestrictions_BODY` is excluded by default. It caps an inspected body at
 * 8 KB, and every resume upload in this product exceeds that. Leaving it in
 * means the upload path is blocked from the first request, which reads as "the
 * product is broken" rather than as "the WAF is on".
 *
 * `CrossSiteScripting_BODY` and `GenericRFI_BODY` are excluded for the reason
 * above: a candidate describing an XSS finding in a security interview is
 * writing about the attack, not performing it, and this product asks people to
 * write about their work in prose.
 *
 * Each exclusion is a NAMED RULE inside a group that otherwise still runs. That
 * is the difference between narrowing a control and removing it, and it is why
 * the exclusions are a variable with a documented default rather than the group
 * simply being left out.
 *
 * THE RATE LIMIT IS THE RULE THAT EARNS ITS PLACE IMMEDIATELY.
 * The public job path is the only unauthenticated, uncredentialed entry point
 * in the product (RBAC §15, and see `modules/alb`), which makes it the one an
 * unattended script will find. A rate-based rule is also the one WAF rule with
 * essentially no false-positive surface: it counts requests per IP and cares
 * nothing about what they contain.
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

locals {
  name = "${var.project}-${var.environment}"

  # COUNT rather than BLOCK when `count_only` is set. Every rule still
  # evaluates, every match is still recorded in the metrics, and nothing is
  # refused. This is the only honest way to size the false-positive rate on a
  # corpus of resumes and interview answers before the rules can reject one.
  override = var.count_only ? "count" : "none"
}

resource "aws_wafv2_web_acl" "this" {
  count = var.enabled ? 1 : 0

  name  = local.name
  scope = "REGIONAL" # An ALB is regional; CLOUDFRONT scope is for distributions.

  # ALLOW BY DEFAULT. A web ACL is a set of exceptions to normal service, not an
  # allowlist: defaulting to block would mean every rule that fails to match
  # takes the site down, which is the opposite of the failure mode wanted from a
  # security control sitting in front of a product.
  default_action {
    allow {}
  }

  # ── Rate limiting ──────────────────────────────────────────────────────────
  rule {
    name     = "rate-limit-per-ip"
    priority = 0

    action {
      dynamic "block" {
        for_each = var.count_only ? [] : [1]
        content {}
      }
      dynamic "count" {
        for_each = var.count_only ? [1] : []
        content {}
      }
    }

    statement {
      rate_based_statement {
        limit              = var.rate_limit_per_five_minutes
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  # ── Managed rule groups ────────────────────────────────────────────────────
  dynamic "rule" {
    for_each = var.managed_rule_groups

    content {
      name     = rule.value.name
      priority = rule.value.priority

      # `override_action` rather than `action`. A managed group's rules carry
      # their own actions; this says whether to respect them or downgrade the
      # whole group to counting.
      override_action {
        dynamic "none" {
          for_each = var.count_only ? [] : [1]
          content {}
        }
        dynamic "count" {
          for_each = var.count_only ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          name        = rule.value.name
          vendor_name = rule.value.vendor_name

          # A NAMED EXCLUSION, not a missing group. See the module docstring:
          # the rest of the group still runs.
          dynamic "rule_action_override" {
            for_each = rule.value.excluded_rules
            content {
              name = rule_action_override.value
              action_to_use {
                count {}
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = replace(rule.value.name, "/[^0-9A-Za-z_-]/", "-")
        sampled_requests_enabled   = true
      }
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = local.name
    # SAMPLED REQUESTS ON. Without them a blocked request is a number on a
    # graph, and "the WAF blocked 400 requests" cannot be turned into "the WAF
    # blocked 400 resume uploads" without re-creating the traffic.
    sampled_requests_enabled = true
  }

  tags = merge(var.tags, { Name = local.name })
}

# ── Logging ──────────────────────────────────────────────────────────────────
#
# THE LOG GROUP NAME IS NOT FREE-FORM. WAF refuses a destination whose name does
# not begin with `aws-waf-logs-`, and the error names the wrong thing, so the
# prefix is applied here rather than left to the caller.

resource "aws_cloudwatch_log_group" "this" {
  count = var.enabled && var.enable_logging ? 1 : 0

  name              = "aws-waf-logs-${local.name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn

  tags = merge(var.tags, { Name = "aws-waf-logs-${local.name}" })
}

resource "aws_wafv2_web_acl_logging_configuration" "this" {
  count = var.enabled && var.enable_logging ? 1 : 0

  resource_arn            = aws_wafv2_web_acl.this[0].arn
  log_destination_configs = [aws_cloudwatch_log_group.this[0].arn]

  # REDACTED, NOT LOGGED. A WAF log records the request that matched, headers
  # included, and this product's requests carry a session cookie and an
  # Authorization header. A security log that captures a live session token
  # turns the log group into a credential store.
  redacted_fields {
    single_header {
      name = "authorization"
    }
  }

  redacted_fields {
    single_header {
      name = "cookie"
    }
  }
}

# ── Association ──────────────────────────────────────────────────────────────

resource "aws_wafv2_web_acl_association" "this" {
  count = var.enabled ? 1 : 0

  resource_arn = var.alb_arn
  web_acl_arn  = aws_wafv2_web_acl.this[0].arn
}
