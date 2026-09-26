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

variable "region" {
  description = "The deployment region. Names the project ARN the role trusts and the Secrets Manager endpoint the one decrypt grant is conditioned on."
  type        = string
}

variable "account_id" {
  description = "Scopes the role's trust policy to this account and this one project (the confused-deputy conditions)."
  type        = string
}

variable "repository_arns" {
  description = "{backend, frontend, analysis} -> ECR repository ARN. EXACTLY those three: the push grant is built from this map and nothing else, so a fourth key would widen it silently."
  type        = map(string)
  validation {
    condition     = length(var.repository_arns) == 3 && alltrue([for name in ["backend", "frontend", "analysis"] : contains(keys(var.repository_arns), name)])
    error_message = "repository_arns must hold exactly backend, frontend and analysis."
  }
}

variable "repository_urls" {
  description = "{backend, frontend, analysis} -> registry URL, handed to the build as plain environment variables so the buildspec names no account."
  type        = map(string)
  validation {
    condition     = length(var.repository_urls) == 3 && alltrue([for name in ["backend", "frontend", "analysis"] : contains(keys(var.repository_urls), name)])
    error_message = "repository_urls must hold exactly backend, frontend and analysis."
  }
}

variable "bucket_name" {
  description = "The private bucket the operator uploads a commit's source archive to. Holds source code only, never customer data, and expires everything after `source_retention_days`."
  type        = string
}

variable "source_retention_days" {
  description = "How long an uploaded source archive survives. Long enough to re-run a build or read what was built; short enough that the bucket is not a second copy of the repository's history."
  type        = number
  default     = 14
  validation {
    condition     = var.source_retention_days >= 1 && var.source_retention_days <= 30
    error_message = "source_retention_days must be between 1 and 30."
  }
}

variable "huggingface_token_secret_arn" {
  description = "The one secret the builder may read: the analysis image's gated model download token, mounted as a BuildKit secret and never written into a layer. Only resolved when a build asks for the analysis image."
  type        = string
}

variable "kms_key_arn" {
  description = "The environment key. Encrypts the build log group, and is the key the token secret and the registries are encrypted with; the role's decrypt grants on it are conditioned on the service they pass through."
  type        = string
}

variable "build_image" {
  description = "The CodeBuild-managed image. aarch64 NATIVE, so an arm64 image builds without QEMU, which is the whole point of the module."
  type        = string
  default     = "aws/codebuild/amazonlinux2-aarch64-standard:3.0"
}

variable "compute_type" {
  description = "BUILD_GENERAL1_LARGE on ARM_CONTAINER. The analysis image compiles torch-scale wheels, and a smaller box moves the two hours this removes back onto the clock."
  type        = string
  default     = "BUILD_GENERAL1_LARGE"
}

variable "build_timeout_minutes" {
  description = "CodeBuild's own ceiling. The operator script waits for less than this by default and STOPS the build when it gives up, so a build nobody is watching cannot push an image later."
  type        = number
  default     = 90
}

variable "queued_timeout_minutes" {
  type    = number
  default = 30
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "tags" {
  type    = map(string)
  default = {}
}
