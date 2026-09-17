/**
 * SES inbound: the address a verification reply comes back to.
 *
 * WHY A SUBDOMAIN, AND NEVER THE APEX.
 * Receiving mail means owning the MX record, and the apex's MX belongs to
 * whatever mailbox the company actually reads. Pointing it here would route the
 * company's own mail into an S3 bucket. `var.reply_domain` is therefore a
 * subdomain of the product's zone, and the MX record this module writes covers
 * only that subdomain.
 *
 * THE RECEIPT RULE MATCHES THE WHOLE DOMAIN, NOT ONE ADDRESS.
 * Every thread has its own address, `conversations+<thread token>@<domain>`,
 * because the address IS the routing: a reply that rewrote the subject, quoted
 * nothing and stripped the body still lands in the right thread. SES matches a
 * recipient by exact address or by domain and offers no pattern in between, so
 * the rule names the domain and the application reads the token.
 *
 * S3 PLUS SNS, NOT A DIRECT LAMBDA ACTION.
 * SES can invoke a function directly and that path caps the message at 256KB. A
 * verification reply with a scanned letter attached is routinely larger, and
 * "it works until somebody attaches something" is the kind of limit discovered
 * by the one reply that mattered. The message is written to S3 whole and the
 * function is told where it is.
 *
 * THE ACTIVE RULE SET IS ACCOUNT-WIDE AND REGION-WIDE, AND THAT IS A REAL
 * CONSTRAINT RATHER THAN A DETAIL. SES allows exactly one active receipt rule
 * set per region per account, so a second environment activating its own would
 * silently stop this one receiving. `var.activate_rule_set` exists for that
 * reason and defaults to false: an environment must SAY it is the one receiving
 * mail in its region, and two environments claiming it is then a merge conflict
 * rather than an outage nobody can see.
 *
 * THE RAW MAIL EXPIRES.
 * What the product keeps is the parsed message in Postgres. The object in this
 * bucket is transport, it holds a third party's words and any file they
 * attached, and keeping it for ever would be a second copy of correspondence
 * nobody manages. `var.retention_days` is the ceiling on that copy.
 */

locals {
  bucket_name = "${var.project}-${var.environment}-inbound-mail"
  prefix      = "inbound/"
}

# ── The domain SES receives for ──────────────────────────────────────────────

resource "aws_ses_domain_identity" "reply" {
  domain = var.reply_domain
}

resource "aws_route53_record" "verification" {
  zone_id = var.hosted_zone_id
  name    = "_amazonses.${var.reply_domain}"
  type    = "TXT"
  ttl     = 600
  records = [aws_ses_domain_identity.reply.verification_token]
}

# WAITS for the token to resolve. Without it the receipt rule can be created
# against an unverified identity, every message is refused at the edge, and
# nothing in the account's own logs says why.
resource "aws_ses_domain_identity_verification" "reply" {
  domain     = aws_ses_domain_identity.reply.id
  depends_on = [aws_route53_record.verification]
}

# THE MX RECORD IS THE CHANGE THE PRODUCT OWNER APPROVED. Mail for the reply
# subdomain goes to SES's regional inbound endpoint and nowhere else.
resource "aws_route53_record" "mx" {
  zone_id = var.hosted_zone_id
  name    = var.reply_domain
  type    = "MX"
  ttl     = 600
  records = ["10 inbound-smtp.${var.region}.amazonaws.com"]
}

# ── Where the raw message lands ──────────────────────────────────────────────

resource "aws_s3_bucket" "mail" {
  bucket = local.bucket_name
  tags   = merge(var.tags, { Purpose = "inbound-mail" })
}

resource "aws_s3_bucket_public_access_block" "mail" {
  bucket                  = aws_s3_bucket.mail.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# VERSIONING, BECAUSE THIS BUCKET IS A RECORD AND NOT A QUEUE.
#
# It looks like a queue: SES writes a message, the parser reads it, a lifecycle
# rule expires it. Nothing in that description needs versioning, which is
# presumably why it was never added.
#
# What the bucket actually holds is the only verbatim copy of an employer's
# reply to a background verification request. `services/bgv` stores what a
# person decided; the raw message is what they decided FROM, and the product
# deliberately infers nothing from it (`test_inbound_conversation_reply.py`
# asserts an arriving reply changes no status). If a recruiter's decision is
# ever questioned, this object is the evidence, and until `retention_days`
# expires it there is exactly one copy.
#
# Without versioning, one overwrite or one delete removes it with nothing left
# behind. The overwrite case is not hypothetical: SES keys an object by the
# message id, SNS delivers AT LEAST ONCE, and the inbound Lambda is idempotent
# on the sender's Message-ID precisely because a redelivery is ordinary.
#
# The cost is bounded by the noncurrent-version rule added to the lifecycle
# configuration below: a noncurrent version of a message is kept for the same
# window as the message, and no longer.
resource "aws_s3_bucket_versioning" "mail" {
  bucket = aws_s3_bucket.mail.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "mail" {
  bucket = aws_s3_bucket.mail.id

  rule {
    apply_server_side_encryption_by_default {
      # AES256 AND NOT THE PLATFORM KMS KEY. SES writes these objects as the
      # SES service principal, and a customer-managed key would need a key
      # policy granting that principal kms:GenerateDataKey. That grant is
      # writable, but it is one more thing that can be wrong in a way whose
      # only symptom is mail silently not arriving.
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "mail" {
  bucket = aws_s3_bucket.mail.id

  # Versioning must exist before the noncurrent-version rule below, or the
  # rule is written against a bucket that has no noncurrent versions and is
  # accepted while reaching nothing. Same ordering the `s3` module states.
  depends_on = [aws_s3_bucket_versioning.mail]

  rule {
    id     = "expire-raw-mail"
    status = "Enabled"

    filter {
      prefix = local.prefix
    }

    expiration {
      days = var.retention_days
    }

    # THE NONCURRENT VERSION EXPIRES TOO, AND IT HAS TO BE SAID.
    #
    # `expiration` on a versioned bucket does not delete anything: it writes a
    # delete marker and the object stays, billed, for ever. A retention
    # decision that silently stops deleting the moment versioning is enabled is
    # the exact shape of a compliance claim nobody can support. Same window as
    # the current version, because the reason for keeping a reply and the
    # reason for keeping its previous copy are the same reason.
    noncurrent_version_expiration {
      noncurrent_days = var.retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # THE SAME ABORT RULE, WITHOUT THE PREFIX, AND IT IS NOT A DUPLICATE.
  #
  # The rule above is scoped to `local.prefix`, so its abort clause reaches
  # multipart uploads under that prefix and nowhere else. A part left by an
  # interrupted write anywhere else in this bucket is then billed for ever:
  # an upload that is never completed and never aborted is storage that no
  # object listing shows and no expiry rule reaches.
  #
  # Scoped for expiry, unscoped for cleanup. The retention decision belongs to
  # the mail prefix; the cleanup belongs to the bucket.
  rule {
    id     = "abort-incomplete-uploads-everywhere"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "mail" {
  # SES may write, only under this prefix, and only on behalf of THIS account.
  # Without the account condition any SES customer in the region could write
  # into the bucket, because `ses.amazonaws.com` is a shared principal.
  statement {
    sid     = "AllowSESPut"
    effect  = "Allow"
    actions = ["s3:PutObject"]

    principals {
      type        = "Service"
      identifiers = ["ses.amazonaws.com"]
    }

    resources = ["${aws_s3_bucket.mail.arn}/${local.prefix}*"]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [var.account_id]
    }
  }

  # The same deny every other bucket in this deployment carries. A deny in a
  # bucket policy cannot be overridden by any IAM grant, which is exactly the
  # property wanted: these objects are a third party's correspondence.
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    resources = [aws_s3_bucket.mail.arn, "${aws_s3_bucket.mail.arn}/*"]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "mail" {
  bucket = aws_s3_bucket.mail.id
  policy = data.aws_iam_policy_document.mail.json

  # The public-access block must be in place BEFORE a policy is attached, or
  # `block_public_policy` can reject the policy on a race.
  depends_on = [aws_s3_bucket_public_access_block.mail]
}

# ── The notification ─────────────────────────────────────────────────────────

# THE TOPIC IS ENCRYPTED WITH THE ENVIRONMENT'S OWN CMK, NOT A KEY OF ITS OWN.
#
# The notification carries who wrote to a verification thread and the S3 key of
# their message, so it is encrypted at rest. Two things decide WHICH key.
#
# It is not `alias/aws/sns`: an AWS-managed key has a fixed policy that cannot
# be granted to another service, so SES could not publish and the symptom would
# be this module's own stated nightmare, mail silently not arriving.
#
# And it is not a key minted here either. The environment already has a CMK
# whose policy names `sns.amazonaws.com` and `ses.amazonaws.com` with an
# account condition, for exactly this reason and in those words. A second key
# would be a second answer to "which key encrypts this environment's data at
# rest", with its own policy to keep in step. The caller passes the ARN.
resource "aws_sns_topic" "received" {
  name              = "${var.project}-${var.environment}-inbound-mail"
  kms_master_key_id = var.kms_key_arn
  tags              = var.tags
}

data "aws_iam_policy_document" "topic" {
  statement {
    effect  = "Allow"
    actions = ["SNS:Publish"]

    principals {
      type        = "Service"
      identifiers = ["ses.amazonaws.com"]
    }

    resources = [aws_sns_topic.received.arn]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "received" {
  arn    = aws_sns_topic.received.arn
  policy = data.aws_iam_policy_document.topic.json
}

resource "aws_sns_topic_subscription" "to_lambda" {
  topic_arn = aws_sns_topic.received.arn
  protocol  = "lambda"
  endpoint  = var.lambda_function_arn
}

resource "aws_lambda_permission" "from_sns" {
  statement_id  = "AllowInboundMailTopic"
  action        = "lambda:InvokeFunction"
  function_name = var.lambda_function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.received.arn
}

# ── The rule ─────────────────────────────────────────────────────────────────

resource "aws_ses_receipt_rule_set" "this" {
  rule_set_name = "${var.project}-${var.environment}-inbound"
}

resource "aws_ses_receipt_rule" "conversations" {
  name          = "conversations"
  rule_set_name = aws_ses_receipt_rule_set.this.rule_set_name
  recipients    = [var.reply_domain]
  enabled       = true

  # TLS REQUIRED. A verification reply carries an employer confirming somebody's
  # employment, and "Optional" means SES accepts it in the clear whenever the
  # sending system declines to negotiate.
  tls_policy   = "Require"
  scan_enabled = true

  s3_action {
    bucket_name       = aws_s3_bucket.mail.id
    object_key_prefix = local.prefix
    topic_arn         = aws_sns_topic.received.arn
    position          = 1
  }

  depends_on = [
    aws_s3_bucket_policy.mail,
    aws_sns_topic_policy.received,
    aws_ses_domain_identity_verification.reply,
  ]
}

# ONE ACTIVE RULE SET PER REGION PER ACCOUNT. See the header: an environment
# must say it is the one receiving, so a second environment cannot take the
# first one's mail by being applied later.
resource "aws_ses_active_receipt_rule_set" "this" {
  count = var.activate_rule_set ? 1 : 0

  rule_set_name = aws_ses_receipt_rule_set.this.rule_set_name
  depends_on    = [aws_ses_receipt_rule.conversations]
}
