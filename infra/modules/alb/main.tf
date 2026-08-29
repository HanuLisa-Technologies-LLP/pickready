/**
 * Application Load Balancer, target groups, listeners and access logs
 * (spec-doc6 §13.2).
 *
 * WHY THIS MODULE EXISTS AT ALL
 * ------------------------------
 * The previous phase built seven modules that stand up a VPC, a cluster, a
 * database, a cache, a registry and a secret store, and then had no way to
 * route a request to any of it. The infrastructure would have come up healthy
 * and served nobody. This is the layer that was missing.
 *
 * THE LOAD BALANCER ROUTES. IT DOES NOT AUTHORIZE.
 * -------------------------------------------------
 * This is the most important thing to understand about the listener rules
 * below, and it is why none of them carries an `authenticate_oidc` or
 * `authenticate_cognito` action.
 *
 * RBAC Specification §33 is explicit: "Authorization must be enforced
 * server-side" and "Obscurity is NOT authorization". If the ALB authenticated,
 * there would be two authorization boundaries, one in the listener rules and
 * one in `require_capability`, and on the day they disagreed the one nobody was
 * reading would win. So the application stays the only authorization boundary,
 * and this module's job is narrower and checkable: route the enumerated public
 * paths and nothing else to the API, and never grow a rule that quietly widens
 * that set.
 *
 * `var.public_path_patterns` is that enumeration. Its validation refuses a
 * pattern broad enough to swallow an authenticated route, and
 * `backend/tests/test_deploy_secret_hygiene.py` reads it back out of this
 * source. The public job page (RBAC §15, "No authentication is required to view
 * the job") is the one product surface that belongs in it.
 *
 * ACCESS LOGS GET THEIR OWN BUCKET, AND IT IS SSE-S3 RATHER THAN SSE-KMS.
 * ------------------------------------------------------------------------
 * Not a preference. The Elastic Load Balancing log delivery service writes with
 * the bucket's default encryption and supports SSE-S3 and SSE-KMS with an
 * AWS-managed key only. A customer-managed key makes every PutObject fail, and
 * it fails SILENTLY: logging stops and the load balancer stays healthy. The
 * application bucket in `modules/s3` is CMK-encrypted because it holds resumes.
 * Access logs are request metadata, so the two buckets have genuinely different
 * requirements and this is not a downgrade of the first one.
 *
 * `drop_invalid_header_fields` and `desync_mitigation_mode = "strictest"` are
 * both on and are the pair worth naming. Without the first, the ALB forwards
 * headers containing invalid characters unchanged, which is the raw material
 * for a request-smuggling desync between the load balancer and the application
 * server. The second closes the same class from the other side.
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

  # Target group and load balancer names are capped at 32 characters by the API,
  # and the error is raised at apply time rather than at plan time, so it is
  # worth not producing one.
  target_group_name = { for key, group in var.target_groups : key => substr("${local.name}-${key}", 0, 32) }
}

# ── Access logs ──────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "logs" {
  bucket        = var.access_logs_bucket_name
  force_destroy = false

  tags = merge(var.tags, { Name = "${local.name}-alb-logs" })
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket                  = aws_s3_bucket.logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# SSE-S3, NOT the environment's customer-managed key. See the module docstring:
# a CMK makes the log delivery service's writes fail, and fail silently.
resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Versioning stays OFF here, unlike the application bucket. An access log object
# is written once and never rewritten, so a version history would hold only
# delete markers, and the lifecycle rule below would then have two things to
# expire instead of one.
resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id
  versioning_configuration {
    status = "Suspended"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    id     = "expire"
    status = "Enabled"
    filter {}
    expiration {
      days = var.access_logs_retention_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.logs]
}

# THE SERVICE PRINCIPAL, NOT `data "aws_elb_service_account"`.
#
# The account-id form is the one every older example uses and it needs a live
# provider lookup, which would break the offline plan spec-doc6 §13.3 requires.
# `logdelivery.elasticloadbalancing.amazonaws.com` is the current mechanism,
# needs no account id, and is constrained by `aws:SourceAccount` below so
# another account's load balancer cannot write here.
data "aws_iam_policy_document" "logs" {
  statement {
    sid    = "AllowLoadBalancerLogDelivery"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["logdelivery.elasticloadbalancing.amazonaws.com"]
    }
    actions = ["s3:PutObject"]
    # Scoped to this bucket's own key space. Not `*`, and not another bucket.
    resources = ["${aws_s3_bucket.logs.arn}/${var.environment}/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }

  # HTTPS ONLY, on the log bucket too. A log line carries a client IP, a request
  # path and a user agent. That is not resume data, and it is not nothing.
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.logs.arn, "${aws_s3_bucket.logs.arn}/*"]
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "logs" {
  bucket = aws_s3_bucket.logs.id
  policy = data.aws_iam_policy_document.logs.json

  depends_on = [aws_s3_bucket_public_access_block.logs]
}

# ── The load balancer ────────────────────────────────────────────────────────

resource "aws_lb" "this" {
  name               = substr(local.name, 0, 32)
  load_balancer_type = "application"
  internal           = false
  subnets            = var.public_subnet_ids
  security_groups    = [var.security_group_id]

  drop_invalid_header_fields = true
  desync_mitigation_mode     = "strictest"

  enable_http2               = true
  enable_deletion_protection = var.enable_deletion_protection

  # LONGER THAN THE LONGEST INTERACTIVE MODEL CALL. `jd_generation` is capped by
  # the router at 25s per attempt and 50s in total (CLAUDE.md, PART B), so an
  # idle timeout below that would drop the connection while the generation a
  # user is watching is still legitimately in flight, and the browser would
  # report it as a network error rather than as a timeout.
  idle_timeout = var.idle_timeout_seconds

  access_logs {
    bucket  = aws_s3_bucket.logs.id
    prefix  = var.environment
    enabled = true
  }

  tags = merge(var.tags, { Name = local.name })

  # The bucket policy must exist before the load balancer, or the first log
  # write is denied and the create fails with an error about the bucket rather
  # than about the ordering.
  depends_on = [aws_s3_bucket_policy.logs]
}

# ── Target groups ────────────────────────────────────────────────────────────

resource "aws_lb_target_group" "this" {
  for_each = var.target_groups

  name        = local.target_group_name[each.key]
  port        = each.value.port
  protocol    = "HTTP"
  target_type = "ip" # Fargate awsvpc: a task is an ENI, not an instance
  vpc_id      = var.vpc_id

  # THE HEALTH CHECK IS THE DEPLOY GATE, so it has to ask a question a broken
  # revision would answer wrongly. `/health` on the API resolves a pooled
  # database session AND issues a broker round trip, so a task whose database
  # credentials or whose Redis endpoint are wrong fails it, and the ECS
  # deployment circuit breaker rolls the deploy back rather than promoting it.
  # A static 200 would promote that same task.
  health_check {
    enabled             = true
    path                = each.value.health_path
    protocol            = "HTTP"
    matcher             = each.value.health_matcher
    interval            = each.value.health_interval_seconds
    timeout             = each.value.health_timeout_seconds
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  # Long enough to finish an in-flight request, short enough that a rolling
  # deploy is not held open by one slow client.
  deregistration_delay = 30

  # NO STICKINESS. Every session in this product is a signed cookie the
  # application validates for itself, so pinning a browser to one task would buy
  # nothing and would concentrate load on whichever task a popular job link
  # happened to hash to.
  stickiness {
    enabled = false
    type    = "lb_cookie"
  }

  tags = merge(var.tags, { Name = local.target_group_name[each.key] })

  lifecycle {
    # A target group is referenced by a listener and by an ECS service, so an
    # in-place replacement would break both for the length of the replacement.
    create_before_destroy = true
  }
}

# ── Listeners ────────────────────────────────────────────────────────────────

# PORT 80 REDIRECTS AND NEVER FORWARDS. There is no path through this listener
# that reaches a target group, so nothing is served in the clear even briefly.
# 301 rather than 302, so a browser stops asking after the first time.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      protocol    = "HTTPS"
      port        = "443"
      status_code = "HTTP_301"
    }
  }

  tags = var.tags
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.certificate_arn

  # TLS 1.2 MINIMUM, FORWARD SECRECY ONLY (spec-doc6 §13.2).
  #
  # `ELBSecurityPolicy-TLS13-1-2-Res-2021-06` negotiates TLS 1.3 and TLS 1.2 and
  # nothing below, and its 1.2 suites are the ECDHE set only: no static RSA key
  # exchange, so a future compromise of the private key does not decrypt traffic
  # captured today. The policy AWS applies when this argument is omitted still
  # negotiates TLS 1.0.
  ssl_policy = var.ssl_policy

  # THE DEFAULT IS THE FRONTEND, NOT THE API. A request that matches no rule is
  # a page request, and the Next.js application renders its own 404 for a page
  # it does not have. Defaulting to the API would answer an unknown page with a
  # JSON error body.
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this[var.default_target_group].arn
  }

  tags = var.tags
}

# ── Listener rules ───────────────────────────────────────────────────────────
#
# PRIORITY IS THE WHOLE MECHANISM. Rules are evaluated lowest number first and
# the first match wins, so the bands below are load-bearing rather than
# cosmetic:
#
#   10..49    the enumerated PUBLIC, unauthenticated paths (RBAC §15)
#   100..199  the rest of the API surface, which the application authorizes
#   default   the frontend
#
# The public band comes first so a later, broader API rule cannot shadow it. The
# gap between the bands is deliberate: a rule inserted without thinking lands in
# neither band, and its author has to choose one.

resource "aws_lb_listener_rule" "public" {
  # ONE RULE PER PATTERN, KEYED BY THE PATTERN. `count` over a list would
  # renumber every rule after any insertion, which in Terraform means destroying
  # and recreating listener rules that did not change.
  for_each = { for index, pattern in var.public_path_patterns : pattern => index }

  listener_arn = aws_lb_listener.https.arn
  priority     = 10 + each.value

  # NO `authenticate_oidc` ACTION, HERE OR ANYWHERE IN THIS MODULE.
  #
  # See the module docstring. These paths are public because the product says a
  # job posting is public (RBAC §15), and what makes RBAC §33 hold is not that
  # the id is hard to guess: it is that the handler behind this path returns a
  # projection carrying no internal field and 404s an unpublished job.
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this[var.public_target_group].arn
  }

  condition {
    path_pattern {
      values = [each.key]
    }
  }

  tags = merge(var.tags, { Access = "public-unauthenticated" })
}

resource "aws_lb_listener_rule" "routes" {
  for_each = var.routes

  listener_arn = aws_lb_listener.https.arn
  priority     = each.value.priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this[each.value.target_group].arn
  }

  condition {
    path_pattern {
      values = each.value.path_patterns
    }
  }

  tags = merge(var.tags, { Access = "application-authorized" })
}
