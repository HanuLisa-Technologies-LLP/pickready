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

  # Prefixes whose objects are SHORT-LIVED by rule, each with its own expiry
  # below. Excluded from the Infrequent Access transition, which carries a
  # 30-day minimum-duration charge an object deleted in hours or days would
  # pay in full for nothing, and which would otherwise overlap an expiry.
  short_lived_prefixes = [
    "project-intake",
    "assessment-raw",
    "assessment-compressed",
    "voice-answers",
  ]
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
    for_each = toset([for prefix in var.application_prefixes : prefix if !contains(local.short_lived_prefixes, prefix)])
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

  # ── Assessment media (owner decision D4) ──────────────────────────────────
  #
  # A HEAD-CONFIRMED DELETE IS NOT A PURGE ON A VERSIONED BUCKET. The delete
  # places a marker and the bytes stay readable at their version id for
  # `noncurrent_retain_days` (thirty in pilot). For a candidate's recording
  # that is thirty days past a deletion the product has reported done, so each
  # media prefix expires its noncurrent versions after ONE day. When two rules
  # apply to one object S3 honours the shorter expiration, so the bucket-wide
  # rule below does not lengthen these.
  rule {
    id     = "expire-assessment-compressed"
    status = "Enabled"

    filter {
      prefix = "assessment-compressed/"
    }

    expiration {
      days = var.assessment_media_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }

  rule {
    id     = "expire-assessment-raw"
    status = "Enabled"

    filter {
      prefix = "assessment-raw/"
    }

    expiration {
      days = var.assessment_raw_backstop_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }

  rule {
    id     = "expire-voice-answers"
    status = "Enabled"

    filter {
      prefix = "voice-answers/"
    }

    expiration {
      days = var.voice_answer_backstop_days
    }

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

  # ENCRYPTION AT REST IS THIS ENVIRONMENT'S KMS KEY, AND THE DENIES BELOW SAY
  # SO WITHOUT REFUSING A MULTIPART UPLOAD (2026-09-24).
  #
  # The previous single statement denied `s3:PutObject` whenever the
  # `x-amz-server-side-encryption` header was not `aws:kms`, using
  # `StringNotEquals`, which also matches when the header is ABSENT. UploadPart
  # and CompleteMultipartUpload authorize as `s3:PutObject` and carry no
  # encryption header (a multipart upload declares its encryption once, at
  # CreateMultipartUpload), so that statement denied every part of every
  # multipart upload: the segmented session recording, and the managed
  # transfer that stores the compressed recording. It never fired only
  # because no recording had ever been written (pilot held zero, CONTRACT v3).
  #
  # So the rule is stated as three facts instead of one header test:
  #   1. a request that NAMES an encryption other than aws:kms is refused;
  #   2. a request that names a KMS key other than this environment's is
  #      refused;
  #   3. a request that names aws:kms WITHOUT a key id is refused, because S3
  #      would then encrypt under the AWS-managed `aws/s3` key rather than
  #      this one.
  # A request that names nothing (every UploadPart) is encrypted by the bucket
  # default above, which is this key, so every object ends up under the one
  # key whichever way it arrived. `services/video/storage._sse` sends both
  # headers on every write that carries them.
  statement {
    sid    = "DenyEncryptionOtherThanKms"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.private.arn}/*"]
    condition {
      test     = "StringNotEqualsIfExists"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }

  statement {
    sid    = "DenyAnotherKmsKey"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.private.arn}/*"]
    condition {
      test     = "StringNotEqualsIfExists"
      variable = "s3:x-amz-server-side-encryption-aws-kms-key-id"
      values   = [var.kms_key_arn]
    }
  }

  statement {
    sid    = "DenyKmsWithoutThisKey"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.private.arn}/*"]
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
    condition {
      test     = "Null"
      variable = "s3:x-amz-server-side-encryption-aws-kms-key-id"
      values   = ["true"]
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
# `application_prefixes` enumerates them; a grant on the whole bucket would
# also cover whatever the next feature puts there, without anybody deciding
# that it should.
#
# THE ENCRYPTION HEADERS THE BUCKET POLICY ACCEPTS are `aws:kms` with THIS
# key's ARN, or none at all (the bucket default, which is this key). Every
# assessment-media write names both (`services/video/storage._sse`,
# S3_KMS_KEY_ID, which must be the key's ARN because the policy compares the
# string); an UploadPart names neither. The `kms:ViaService` statement below
# is what lets the application use the key through S3 for both.
data "aws_iam_policy_document" "application" {
  statement {
    sid    = "ReadWriteTheApplicationPrefixes"
    effect = "Allow"
    # The two multipart actions are the session recording's: a segment is one
    # multipart upload (CreateMultipartUpload, UploadPart and
    # CompleteMultipartUpload authorize as s3:PutObject), the sweep lists an
    # abandoned upload's parts to complete it without the browser, and a
    # segment that never received a part is aborted rather than left billed.
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
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
