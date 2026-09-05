variable "project" {
  type = string
}

variable "environment" {
  type = string
}

variable "account_id" {
  description = "Used to scope the scheduler's assume-role trust to this account. See the confused-deputy condition in main.tf."
  type        = string
}

variable "target_function_arn" {
  description = "The generic task worker. One target, named, so a schedule cannot be repointed at an arbitrary function by editing a string."
  type        = string
}

variable "timezone" {
  description = "The platform's wall clock, mirroring app.core.config.PLATFORM_TIMEZONE. Irrelevant to a rate() expression and set anyway, so a future cron() entry does not silently land in UTC."
  type        = string
  default     = "Asia/Kolkata"
}

variable "schedules" {
  description = "{rule name -> {task, rate_expression}}. Mirrors app/workers/schedule.py; the parity test compares them."
  type = map(object({
    task            = string
    rate_expression = string
  }))
}

variable "enabled" {
  description = "Set false to stop every sweep at once, which is the switch to reach for during an incident rather than deleting the schedules."
  type        = bool
  default     = true
}

variable "tags" {
  type    = map(string)
  default = {}
}
