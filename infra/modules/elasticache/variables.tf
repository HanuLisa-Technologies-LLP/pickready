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

variable "node_type" {
  type = string
}

variable "engine_version" {
  type    = string
  default = "7.1"
}

variable "replica_count" {
  description = "Replicas per shard. Zero in the pilot and in staging; at least one in production, because a Redis failure there is not a cache miss: the proctoring gate answers 503 rather than silently not warning, so it is every assessment turn."
  type        = number
  default     = 0
}

variable "kms_key_arn" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
