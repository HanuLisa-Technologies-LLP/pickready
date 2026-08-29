variable "project" {
  type    = string
  default = "readypick"
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "region" {
  type = string
}

variable "bucket_name" {
  description = "Override the derived name. S3 bucket names are globally unique, so a fresh account may need one."
  type        = string
  default     = ""
}

variable "application_prefixes" {
  description = <<-EOT
    The prefixes `services/object_storage` actually uses, and nothing else.

    A grant on the whole bucket would also cover whatever the next feature puts
    there, without anybody deciding that it should. Adding a prefix here is a
    Terraform change with a readable plan, which is the point.
  EOT
  type        = list(string)
  default     = ["resumes", "compliance"]
}

variable "noncurrent_retain_days" {
  description = "How long an accidental delete stays recoverable. Versioning is on for this reason rather than for rollback -- objects are content-addressed, so the same key always holds the same bytes."
  type        = number
  default     = 30
}

variable "kms_key_arn" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
