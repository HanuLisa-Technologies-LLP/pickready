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

variable "node_type" {
  type = string
}

variable "engine_version" {
  type    = string
  default = "7.1"
}

variable "replica_count" {
  description = "Replicas per shard. Zero in staging; at least one in production, because a Redis failure there is not a cache miss -- it is every Celery task and every working-memory read."
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
