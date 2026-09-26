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

variable "subnet_ids" {
  description = "The DATA subnets. No route to the internet in either direction."
  type        = list(string)
}

variable "security_group_id" {
  type = string
}

variable "engine_version" {
  description = "Major version only. The minor moves under auto_minor_version_upgrade and is ignored as drift."
  type        = string
  default     = "16"
}

variable "parameter_group_family" {
  type    = string
  default = "postgres16"
}

variable "instance_class" {
  type = string
}

variable "allocated_storage" {
  type    = number
  default = 50
}

variable "max_allocated_storage" {
  description = "Storage autoscaling ceiling. Set rather than unbounded: an unbounded ceiling turns a runaway write loop into a bill nobody notices until it arrives."
  type        = number
  default     = 200
}

variable "database_name" {
  type    = string
  default = "readypick"
}

variable "master_username" {
  description = "NOT the application credential. The application uses a least-privileged role whose DSN is its own secret; the master exists to create that role and to run migrations."
  type        = string
  default     = "readypick_admin"
}

variable "multi_az" {
  type    = bool
  default = false
}

variable "backup_retention_days" {
  type    = number
  default = 7
}

variable "snapshot_suffix" {
  description = "Appended to the final-snapshot identifier, which must be unique. Supplied rather than derived from a timestamp, because a timestamp in a plan makes every plan show a diff. BUMP IT after a destroy-and-recreate cycle: AWS keeps the old snapshot and refuses a second one under the same name, so an unbumped suffix turns the next teardown into a failed delete."
  type        = string
  default     = "v1"
}

variable "deletion_protection" {
  description = <<-EOT
    Whether RDS refuses a delete API call outright.

    TRUE BY DEFAULT, AND THE DEFAULT IS THE FIX. This was keyed on
    `environment == "production"` until 2026-09-17, which protected the one
    environment that has never been applied and left unprotected the one that
    actually exists and holds data.

    Turning it off is a real decision with a real use: an environment that is
    torn down and rebuilt as part of its normal life cannot be, while this is
    on, without a separate apply to flip it first. That extra apply is the
    point. Set it to false in an environment root, with a comment saying why,
    and never as a default here.
  EOT
  type        = bool
  default     = true
}

variable "skip_final_snapshot" {
  description = <<-EOT
    Whether a delete is allowed to proceed WITHOUT capturing the database.

    FALSE BY DEFAULT, which means a snapshot is always taken. It is the second
    of the two guards and it is the one that survives the first being turned
    off: deletion protection stops the call, and this makes the call
    recoverable when somebody has already decided to make it.

    It is NOT redundant with `backup_retention_days`. Automated backups are
    DELETED WITH THE INSTANCE unless a final snapshot is taken, so an
    environment with thirty days of backups and `skip_final_snapshot = true`
    has thirty days of backups right up until the moment they would be useful.

    Setting it true costs the snapshot's storage and nothing else, so an
    environment opting out is trading recoverability for a few dollars.
  EOT
  type        = bool
  default     = false
}

variable "kms_key_id" {
  type = string
}

variable "kms_key_arn" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
