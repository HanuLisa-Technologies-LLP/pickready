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
  # Read off the code that writes them: `resume_storage.OBJECT_PREFIX`,
  # `document_storage.OBJECT_PREFIX` and `projects/intake.INTAKE_PREFIX`. The
  # third was missing until 2026-09-05, so every candidate project upload would
  # have been refused with AccessDenied in a feature that ships. It was never
  # found because no environment had ever been applied.
  default = ["resumes", "compliance", "project-intake"]
}

variable "project_intake_backstop_days" {
  description = <<-EOT
    How long a temporary project original may survive under `project-intake/`
    before the bucket deletes it regardless.

    A BACKSTOP, not an archive. Originals are staged temporarily and deleted
    with a HEAD check confirming each deletion; a failed deletion is counted on
    the row and retried hourly by `pickready.reconcile_project_intake`. This
    catches the case where the delete failed AND the reconciler never ran.

    It DELETES. It does not transition, retain or archive, so it does not
    reintroduce the original-project store the Project Evidence brief refuses.
    Seven days is far longer than the hourly sweep needs and short enough that
    a stuck original is not a standing liability.
  EOT
  type        = number
  default     = 7
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
