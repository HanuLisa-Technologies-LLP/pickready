/**
 * Container registries, one per image.
 *
 * TAGS ARE IMMUTABLE, AND THAT IS THE LOAD-BEARING SETTING HERE.
 *
 * spec-doc5 §D.5 requires images "tagged by commit SHA, not `latest`", and §D.6
 * requires verification "by image digest, not by CI exit code". Neither is worth
 * anything if a tag can be moved: `IMMUTABLE` is what makes a SHA tag a
 * permanent name for a specific set of bytes, so "confirm the running task is
 * the commit that was reviewed" is a question with an answer.
 *
 * With MUTABLE tags, a re-run of a workflow on the same commit overwrites the
 * tag, and the digest a reviewer verified is silently no longer what that tag
 * points at. The push simply fails here instead, which is the correct outcome:
 * a second build of the same commit that produces different bytes is a
 * reproducibility problem worth failing on.
 *
 * SCAN ON PUSH IS ON. It costs nothing and finds the CVE in a base image that
 * nobody would otherwise look for until an audit.
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
}

resource "aws_ecr_repository" "this" {
  for_each = toset(var.repositories)

  name = "${local.name}/${each.value}"

  # See the module docstring. This is what makes a SHA tag mean something.
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = var.kms_key_arn
  }

  tags = merge(var.tags, { Name = "${local.name}-${each.value}" })
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name

  # UNTAGGED IMAGES GO FIRST AND FAST. They are build layers orphaned by a
  # subsequent push and nothing can ever run them.
  #
  # TAGGED IMAGES ARE KEPT BY COUNT, NOT BY AGE. An age rule would delete the
  # image a long-lived production service is still running, purely because the
  # deploy was a while ago -- and the failure surfaces as a task that cannot
  # restart, at 3am, on the image that had been working for months.
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Untagged layers orphaned by a later push. Nothing can run them."
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 3
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the last N tagged images. By COUNT, never by age: an age rule deletes the image a long-running service still needs to restart from."
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["sha-"]
          countType     = "imageCountMoreThan"
          countNumber   = var.keep_images
        }
        action = { type = "expire" }
      },
    ]
  })
}
