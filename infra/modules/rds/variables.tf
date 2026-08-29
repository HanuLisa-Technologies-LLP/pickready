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
  description = "Appended to the production final-snapshot identifier, which must be unique. Supplied rather than derived from a timestamp, because a timestamp in a plan makes every plan show a diff."
  type        = string
  default     = "v1"
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
