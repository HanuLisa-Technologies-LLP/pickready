/**
 * The image builder: a native arm64 CodeBuild project that builds and pushes
 * the backend, the Lambda `-fn` sibling, the frontend and (on request) the
 * analysis image, so a deploy stops spending two hours in QEMU emulation on
 * the operator's x86 laptop.
 *
 * WHAT IT IS GIVEN, AND ONLY THAT
 * --------------------------------
 * `scripts/build-images-remote.sh` uploads `git archive` of ONE exact commit
 * to this module's private bucket under `builds/` and starts a build with the
 * object key as the source. The build never sees a working tree, a `.git`
 * directory or an uncommitted edit, so what it pushes under `sha-<commit>` is
 * the committed bytes and nothing else.
 *
 * THE ROLE IS THE BOUNDARY
 * -------------------------
 *   ECR        push and pull on the backend, frontend and analysis
 *              repositories, by ARN. Not the Judge0 mirrors, not any other
 *              repository in the account.
 *   S3         s3:GetObject under `builds/` in THIS bucket. No list, no put,
 *              no delete, and nothing in the storage bucket that holds
 *              candidate data.
 *   Logs       its own log group, which has a retention.
 *   Secrets    the Hugging Face download token, and nothing else. Decrypting
 *              it is conditioned on Secrets Manager and on that secret's ARN.
 *
 * It holds no database credential, no model key and no deploy permission: it
 * cannot update a service, a function or a task definition. Deploying remains
 * the operator's act, verified by digest afterwards.
 *
 * WHY PRIVILEGED, AND WHY ONLY HERE
 * ----------------------------------
 * Building a container image needs the Docker daemon, and CodeBuild runs the
 * daemon only in a privileged build container. The build is an ephemeral
 * managed host that runs our own committed Dockerfiles and holds the grants
 * above and nothing else, so it is a build host, not a runtime: nothing a
 * candidate or a tenant supplies ever reaches it.
 *
 * THE BUILDSPEC IS THE MODULE'S, NOT THE COMMIT'S
 * ------------------------------------------------
 * `buildspec.yml` beside this file is embedded in the project at apply time.
 * The source archive carries only the three build contexts, so a commit cannot
 * change how it is built. The operator script compares the applied buildspec
 * with the one in the commit being built and refuses on a difference, because
 * "the buildspec I just edited" and "the buildspec that ran" must not be two
 * different files without anybody noticing.
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
  name          = "${var.project}-${var.environment}-image-builder"
  source_prefix = "builds/"
  repositories  = ["backend", "frontend", "analysis"]

  # Written out rather than read from the project, which would be a cycle: the
  # role's trust policy names the one project allowed to assume it.
  project_arn = "arn:aws:codebuild:${var.region}:${var.account_id}:project/${local.name}"
}

# ── The source bucket ────────────────────────────────────────────────────────

resource "aws_s3_bucket" "source" {
  bucket = var.bucket_name
  tags   = merge(var.tags, { Name = local.name })
}

resource "aws_s3_bucket_public_access_block" "source" {
  bucket                  = aws_s3_bucket.source.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "source" {
  bucket = aws_s3_bucket.source.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# SSE-S3, NOT the environment key. The bucket holds source code, which is not
# customer data, and encrypting it with the environment key would mean giving
# the builder a decrypt grant on the key that also protects the database and
# every secret. Encrypted at rest either way.
resource "aws_s3_bucket_server_side_encryption_configuration" "source" {
  bucket = aws_s3_bucket.source.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "source" {
  bucket = aws_s3_bucket.source.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "source" {
  # The versioning resource first, or the noncurrent rule applies to a bucket
  # that has no noncurrent versions yet.
  depends_on = [aws_s3_bucket_versioning.source]
  bucket     = aws_s3_bucket.source.id

  rule {
    id     = "expire-source-archives"
    status = "Enabled"
    filter {}

    expiration {
      days = var.source_retention_days
    }
    # Versioning turns an expiry into a delete marker over a noncurrent copy,
    # so the copy needs its own rule or "expires after 14 days" is true of the
    # listing and false of the bytes.
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  rule {
    id     = "remove-expired-delete-markers"
    status = "Enabled"
    filter {}
    expiration {
      expired_object_delete_marker = true
    }
  }
}

data "aws_iam_policy_document" "source_bucket" {
  statement {
    sid     = "RefusePlaintextTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = [
      aws_s3_bucket.source.arn,
      "${aws_s3_bucket.source.arn}/*",
    ]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "source" {
  bucket = aws_s3_bucket.source.id
  policy = data.aws_iam_policy_document.source_bucket.json

  # A bucket policy written before the public access block is in place is
  # evaluated against a bucket that does not yet refuse public policies.
  depends_on = [aws_s3_bucket_public_access_block.source]
}

# ── Logs ─────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "build" {
  name              = "/aws/codebuild/${local.name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = var.tags
}

# ── The role ─────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
    # The confused-deputy conditions: this account, and this one project. A
    # service principal with no condition is assumable by CodeBuild on behalf
    # of any project that names the role.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = [local.project_arn]
    }
  }
}

resource "aws_iam_role" "build" {
  name               = local.name
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "build" {
  statement {
    sid       = "RegistryLogin"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PushAndPullTheThreeProductRepositoriesOnly"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
    ]
    resources = [for name in local.repositories : var.repository_arns[name]]
  }

  statement {
    sid       = "ReadSourceArchivesUnderBuildsOnly"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.source.arn}/${local.source_prefix}*"]
  }

  statement {
    sid    = "WriteThisBuildsLogGroupOnly"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.build.arn}:*"]
  }

  statement {
    sid       = "ReadTheModelDownloadTokenOnly"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.huggingface_token_secret_arn]
  }

  statement {
    sid       = "DecryptThatTokenThroughSecretsManagerOnly"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [var.kms_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.region}.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:SecretARN"
      values   = [var.huggingface_token_secret_arn]
    }
  }

  # The registries are encrypted with the environment key. ECR's own grant on
  # the key normally carries a push; this is the belt beside it, the same
  # ViaService shape the code sandbox host uses for its pulls, and it cannot
  # reach anything but ECR.
  statement {
    sid    = "LayerCryptoThroughEcrOnly"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
    ]
    resources = [var.kms_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ecr.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "build" {
  name   = local.name
  role   = aws_iam_role.build.id
  policy = data.aws_iam_policy_document.build.json
}

# ── The project ──────────────────────────────────────────────────────────────

resource "aws_codebuild_project" "this" {
  name          = local.name
  description   = "Native arm64 image build for ${var.project}-${var.environment}. Started by scripts/build-images-remote.sh with one commit's source archive."
  service_role  = aws_iam_role.build.arn
  build_timeout = var.build_timeout_minutes

  queued_timeout = var.queued_timeout_minutes

  # ONE AT A TIME. Tags are immutable, so two builds of one commit race to a
  # push only one can win, and the loser's failure reads like a broken build.
  concurrent_build_limit = 1

  artifacts {
    type = "NO_ARTIFACTS"
  }

  # Docker layer cache on the build host, while CodeBuild keeps the host warm.
  # A cache hit reuses a layer the same Dockerfile produced; it never changes
  # what a Dockerfile means, only how long it takes.
  cache {
    type  = "LOCAL"
    modes = ["LOCAL_DOCKER_LAYER_CACHE"]
  }

  environment {
    type                        = "ARM_CONTAINER"
    compute_type                = var.compute_type
    image                       = var.build_image
    image_pull_credentials_type = "CODEBUILD"
    # The Docker daemon runs only in a privileged build container. See the
    # module docstring for why this is the one place that is acceptable.
    privileged_mode = true

    environment_variable {
      name  = "REGISTRY"
      value = split("/", var.repository_urls["backend"])[0]
    }
    environment_variable {
      name  = "BACKEND_REPOSITORY_URI"
      value = var.repository_urls["backend"]
    }
    environment_variable {
      name  = "FRONTEND_REPOSITORY_URI"
      value = var.repository_urls["frontend"]
    }
    environment_variable {
      name  = "ANALYSIS_REPOSITORY_URI"
      value = var.repository_urls["analysis"]
    }
    # NAMES, not values. The script reads these back from the project so it
    # needs no Terraform state; the token itself is resolved by CodeBuild only
    # when a build overrides HUGGINGFACE_TOKEN with type SECRETS_MANAGER.
    environment_variable {
      name  = "SOURCE_BUCKET"
      value = aws_s3_bucket.source.id
    }
    environment_variable {
      name  = "HUGGINGFACE_TOKEN_SECRET_ARN"
      value = var.huggingface_token_secret_arn
    }
  }

  logs_config {
    cloudwatch_logs {
      status     = "ENABLED"
      group_name = aws_cloudwatch_log_group.build.name
    }
    s3_logs {
      status = "DISABLED"
    }
  }

  source {
    type = "S3"
    # A key that is never uploaded. Every real build overrides the location
    # with one commit's archive, so a build started from the console with no
    # override fails at DOWNLOAD_SOURCE rather than building something stale.
    location  = "${aws_s3_bucket.source.id}/${local.source_prefix}start-with-scripts-build-images-remote.zip"
    buildspec = file("${path.module}/buildspec.yml")
  }

  tags = var.tags

  # The policy must exist before the first build assumes the role, or the
  # first build fails on permissions that an apply later would have granted.
  depends_on = [aws_iam_role_policy.build]
}
