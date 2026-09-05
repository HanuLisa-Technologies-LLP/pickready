/**
 * S3: resumes, work-sample artefacts, PRISM attachments, evidence-graph sources.
 *
 * ONE PRIVATE BUCKET, AND EVERY PUBLIC-ACCESS DOOR EXPLICITLY SHUT.
 * `aws_s3_bucket_public_access_block` sets all four flags rather than relying on
 * the account-level default, because an account-level default is a setting
 * somebody can change for an unrelated reason and this bucket holds candidates'
 * resumes. Four flags stated on the bucket cannot be turned off by a change
 * somewhere else.
 *
 * THE BUCKET POLICY DENIES UNENCRYPTED TRANSPORT, AND THAT IS THE ONE THAT
 * MATTERS. Encryption at rest is on and would be on anyway; a `DENY` on
 * `aws:SecureTransport = false` is what stops a misconfigured client reading a
 * resume over plain HTTP. A deny in a bucket policy cannot be overridden by any
 * IAM grant, which is exactly the property wanted here.
 *
 * VERSIONING IS ON, AND IT IS NOT FOR ROLLBACK.
 * Objects are CONTENT-ADDRESSED by sha256, so the same key always holds the
 * same bytes and there is nothing to roll back to. Versioning is on because it
 * makes a DELETE recoverable: a delete places a marker rather than destroying
 * the object, and the delete this protects against is the accidental one during
 * a cleanup script, not an attacker.
 *
 * THE LIFECYCLE RULES ARE ABOUT COST, AND ONE IS ABOUT CORRECTNESS.
 * Transitioning to Infrequent Access after 90 days is cost. Aborting incomplete
 * multipart uploads after 7 days is CORRECTNESS: an aborted large upload leaves
 * parts that are billed and are invisible in the console object list, which is
 * the classic S3 bill nobody can explain.
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
  name = "${var.project}-${var.environment}-private"
}

resource "aws_s3_bucket" "private" {
  bucket = var.bucket_name != "" ? var.bucket_name : local.name

  tags = merge(var.tags, { Name = local.name })
}

resource "aws_s3_bucket_public_access_block" "private" {
  bucket = aws_s3_bucket.private.id

  # All four, stated on the bucket. See the module docstring.
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "private" {
  bucket = aws_s3_bucket.private.id
  rule {
    # ACLs are disabled outright. Every access decision is an IAM or bucket
    # policy decision, which means every access decision is reviewable in one
    # place rather than in two systems that can disagree.
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "private" {
  bucket = aws_s3_bucket.private.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    # A per-object KMS call for every read would be a real cost at resume
    # volume. A bucket key caches the data key, cutting KMS requests by
    # roughly 99%.
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "private" {
  bucket = aws_s3_bucket.private.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "private" {
  bucket = aws_s3_bucket.private.id
  # The versioning resource must exist first, or the noncurrent-version rules
  # below apply to a bucket that has no noncurrent versions.
  depends_on = [aws_s3_bucket_versioning.private]

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}

    # CORRECTNESS, NOT COST. Parts from an aborted upload are billed and are
    # invisible in the console object list -- the classic S3 bill nobody can
    # explain.
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # ONE RULE PER DURABLE PREFIX, rather than one bucket-wide rule.
  #
  # S3 lifecycle filters have no negation: `Filter` takes a prefix, tags, a
  # size, or an `And` of those, and there is no `Not`. So `project-intake/` is
  # excluded by ENUMERATING what is included instead, which is more explicit
  # anyway and matches how `application_prefixes` grants access.
  #
  # It has to be excluded: a transition to STANDARD_IA carries a 30-day
  # minimum-duration charge, and a temporary project original that lives for
  # minutes would pay it in full for nothing.
  dynamic "rule" {
    for_each = toset([for prefix in var.application_prefixes : prefix if prefix != "project-intake"])
    content {
      id     = "cool-old-objects-${rule.value}"
      status = "Enabled"

      filter {
        prefix = "${rule.value}/"
      }

      transition {
        days          = 90
        storage_class = "STANDARD_IA"
      }
    }
  }

  # THE BACKSTOP FOR TEMPORARY PROJECT ORIGINALS. See
  # `project_intake_backstop_days`: it deletes, it does not archive, and it
  # exists for the case where the verified deletion failed and the hourly
  # reconciler never ran.
  rule {
    id     = "expire-project-intake"
    status = "Enabled"

    filter {
      prefix = "project-intake/"
    }

    expiration {
      days = var.project_intake_backstop_days
    }

    # The noncurrent version too. Versioning is on for this bucket, so a plain
    # delete leaves a version behind, and a deleted original that is still
    # readable at a version id is an original that was not deleted.
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }

  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}

    # A noncurrent version exists because something was deleted or replaced.
    # `retain_days` is how long an accidental delete stays recoverable, and it
    # is longer in production for the obvious reason.
    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_retain_days
    }
  }
}

data "aws_iam_policy_document" "private" {
  # THE DENY THAT MATTERS. It cannot be overridden by any IAM grant, which is
  # exactly the property wanted: a misconfigured client cannot read a resume in
  # the clear even if its role would otherwise allow the read.
  statement {
    sid    = "DenyUnencryptedTransport"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.private.arn,
      "${aws_s3_bucket.private.arn}/*",
    ]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid    = "DenyUnencryptedObjectUploads"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.private.arn}/*"]
    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }
}

resource "aws_s3_bucket_policy" "private" {
  bucket = aws_s3_bucket.private.id
  policy = data.aws_iam_policy_document.private.json

  # The public-access block must land before the policy, or the policy is
  # evaluated against a bucket that briefly permits a public policy.
  depends_on = [aws_s3_bucket_public_access_block.private]
}

# ── The application's grant ──────────────────────────────────────────────────
#
# SCOPED TO THE PREFIXES THE APPLICATION ACTUALLY WRITES, not to the bucket.
# `resumes/*` and `compliance/*` are the two `object_storage` uses; a grant on
# the whole bucket would also cover whatever the next feature puts there,
# without anybody deciding that it should.
data "aws_iam_policy_document" "application" {
  statement {
    sid    = "ReadWriteTheApplicationPrefixes"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = [
      for prefix in var.application_prefixes :
      "${aws_s3_bucket.private.arn}/${prefix}/*"
    ]
  }

  # HeadObject and the content-addressed put both need this, and it is on the
  # BUCKET rather than on the objects.
  statement {
    sid       = "ListForTheContentAddressedPut"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.private.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = [for prefix in var.application_prefixes : "${prefix}/*"]
    }
  }

  statement {
    sid    = "UseTheBucketKey"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
    ]
    resources = [var.kms_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_policy" "application" {
  name        = "${local.name}-object-access"
  description = "Read/write on exactly the prefixes the application uses. Not the whole bucket."
  policy      = data.aws_iam_policy_document.application.json
  tags        = var.tags
}
