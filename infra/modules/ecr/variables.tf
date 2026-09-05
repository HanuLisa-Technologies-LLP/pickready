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

variable "repositories" {
  description = "One per image. The worker and beat run the backend image with a different command, so they are not separate repositories. `analysis` is the proctoring analysis service (analysis-service/), a separate image because it carries the model libraries the backend never loads."
  type        = list(string)
  default     = ["backend", "frontend", "analysis"]
}

variable "keep_images" {
  description = "Tagged images retained per repository. Enough to roll back several deploys, not enough to pay to store a year of them."
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
