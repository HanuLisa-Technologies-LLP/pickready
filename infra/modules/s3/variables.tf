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
  #
  # THE SAME DEFECT, AGAIN, FOR ASSESSMENT MEDIA (2026-09-24). The session
  # recording has been written under `assessment-raw/` and
  # `assessment-compressed/` (`services/video/keys.py`) since 2026-09-22, and
  # neither prefix was here, so every recording upload would have been refused
  # at the IAM grant as well as at the bucket policy's encryption header.
  # Pilot held zero recordings when it was found. `voice-answers/` is the
  # spoken-answer audio, held only until Amazon Transcribe has read it.
  default = [
    "resumes",
    "compliance",
    "project-intake",
    "assessment-raw",
    "assessment-compressed",
    "voice-answers",
  ]
}

variable "assessment_media_retention_days" {
  description = <<-EOT
    Owner decision D4, the BACKSTOP half. A stored session recording under
    `assessment-compressed/` expires this many days after the object was
    written.

    The DATABASE is what purges on time: `media_purge_due_at` is stamped on
    each recording at the session's end and the hourly
    `pickready.purge_assessment_media` deletes it HEAD-confirmed, earlier
    still when the job closes. The compressed object is always written after
    the session ended, so this rule can never fire before the stored date,
    only after it, which is what makes it a backstop for a sweep that did not
    run rather than a second clock. Must equal the application's
    `assessment_media_retention_days`; `tests/test_media_retention_d4.py`
    compares the two.
  EOT
  type        = number
  default     = 90
}

variable "assessment_raw_backstop_days" {
  description = <<-EOT
    How long a raw recording segment may survive under `assessment-raw/`.

    Raw segments are a processing artifact: deleted, HEAD-confirmed, as soon
    as the compressed recording is verified, and retried hourly by
    `pickready.reconcile_assessment_recordings` when a deletion did not
    confirm. Seven days also bounds how long a recording whose processing
    failed can wait for the staff retry, which is the trade: a candidate's raw
    video does not sit in the bucket because a pipeline stalled.
  EOT
  type        = number
  default     = 7
}

variable "voice_answer_backstop_days" {
  description = <<-EOT
    How long spoken-answer audio may survive under `voice-answers/`. The
    transcript is the stored answer; the audio exists only until Amazon
    Transcribe has read it and is deleted HEAD-confirmed right after.
  EOT
  type        = number
  default     = 1
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
