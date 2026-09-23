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
  description = "The deployment region. Names the S3 buckets the sandbox may read and the automate ARNs its alarms act through."
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "availability_zone" {
  description = "ONE zone. The box is stateless and replaced rather than repaired, so a second zone buys nothing a replacement does not."
  type        = string
}

variable "sandbox_cidr_block" {
  description = "The sandbox subnet. Its route table carries the VPC-local route and the S3 gateway endpoint, and nothing else."
  type        = string
}

variable "private_subnet_cidr_blocks" {
  description = "The application tier. The only source the network ACL admits on the sandbox port, and where the interface endpoints answer."
  type        = list(string)
}

variable "data_subnet_cidr_blocks" {
  description = "RDS and ElastiCache. DENIED in both directions by the network ACL, ahead of every allow."
  type        = list(string)
}

variable "interface_endpoints_security_group_id" {
  description = <<-EOT
    The VPC's existing interface endpoints (ECR api and dkr, Secrets Manager,
    Logs), REUSED rather than duplicated: a second interface endpoint for the
    same service cannot also hold its private DNS name, and duplicates would be
    roughly seven dollars a month each for nothing. The caller must ALSO admit
    the host group on that security group; see the network module's
    `endpoint_client_security_group_ids`.
  EOT
  type        = string
}

variable "discovery_namespace_id" {
  description = "The internal Cloud Map namespace the host registers in as `judge0`."
  type        = string
  default     = null
}

variable "discovery_namespace_name" {
  description = "The namespace's DNS name, for the `sandbox_url` output."
  type        = string
  default     = null
}

variable "register_in_namespace" {
  description = "A literal rather than `discovery_namespace_id != null`, because the namespace id is unknown until it exists and a count cannot depend on an unknown."
  type        = bool
  default     = true
}

variable "kms_key_arn" {
  description = "Encrypts the root volume, the images, the token and the log group."
  type        = string
}

variable "kms_key_id" {
  type = string
}

variable "alarm_topic_arn" {
  type = string
}

variable "create_instance" {
  description = <<-EOT
    False creates everything EXCEPT the instance: the network, the security
    groups, the role, the registries and the token. The images must be mirrored
    into the registries before a host can pull them, so the first apply stops
    here and the second, with the digests, creates the box.
  EOT
  type        = bool
  default     = false
}

variable "ami_id" {
  description = <<-EOT
    Amazon Linux 2023 x86_64, PINNED. A variable, never an SSM parameter or an
    `aws_ami` lookup, for two reasons: the offline plan cannot call AWS, and a
    newly published "recommended" image must never replace the host in an apply
    nobody meant to be a replacement. The pinned release also pins the Docker
    package the host installs, because AL2023 repositories are versioned by
    release. Empty is allowed only while `create_instance` is false.
  EOT
  type        = string
  default     = ""
  validation {
    condition     = var.ami_id == "" || can(regex("^ami-[0-9a-f]{8,17}$", var.ami_id))
    error_message = "ami_id must be an AMI id (ami- followed by hex)."
  }
}

variable "instance_type" {
  description = "t3.medium: Judge0's server, two workers, its Postgres and Redis and a JVM compile do not fit in 2 GiB."
  type        = string
  default     = "t3.medium"
  validation {
    condition     = can(regex("^[a-z][a-z0-9]*\\.[a-z0-9]+$", var.instance_type))
    error_message = "instance_type must be an EC2 instance type."
  }
}

variable "root_volume_gb" {
  description = "The Judge0 image is several gigabytes, and the host keeps its logs on disk rather than shipping them."
  type        = number
  default     = 40
}

variable "image_digests" {
  description = <<-EOT
    {judge0, judge0-postgres, judge0-redis} -> the sha256 digest of the image
    mirrored into this module's registry of that name
    (scripts/mirror-judge0-images.sh prints them). The host pulls BY DIGEST,
    so a tag moved upstream can never change what runs here.
  EOT
  type        = map(string)
  default     = {}
  validation {
    condition     = alltrue([for digest in values(var.image_digests) : can(regex("^sha256:[0-9a-f]{64}$", digest))])
    error_message = "every image digest must be sha256: followed by 64 hex characters."
  }
}

variable "package_repository_bucket_arns" {
  description = <<-EOT
    The S3 buckets that serve the Amazon Linux 2023 package repositories in this
    region, as object ARNs. Empty derives AWS's documented name
    (`al2023-repos-<region>-de612dc2`). CONFIRM AT THE S1 REVIEW against the
    AL2023 documentation for the region before the first apply: a wrong name
    makes the first boot fail to install Docker, loudly, and nothing else.
  EOT
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "cpu_credit_alarm_threshold" {
  description = "Standard credits, so a busy box THROTTLES rather than billing. This alarm is how throttling becomes visible before candidates see time-limit failures."
  type        = number
  default     = 30
}

variable "tags" {
  type    = map(string)
  default = {}
}
