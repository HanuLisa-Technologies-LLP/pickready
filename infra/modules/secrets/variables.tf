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

variable "secret_names" {
  description = <<-EOT
    Every secret this environment holds. spec-doc5 §B.5 puts the platform at
    THREE credentials -- OPENAI_GPT_TERRA and OPENAI_GPT_LUNA for the two
    model tiers, and VOYAGE_CONTEXT_4 for embeddings -- and the
    rest of this list is what the product already needed.

    The container is created here; the VALUE is not. A value in Terraform is a
    value in the state file, and the state file is JSON behind a bucket policy
    rather than a vault.
  EOT
  type        = list(string)
  default = [
    "OPENAI_GPT_TERRA",
    "OPENAI_GPT_LUNA",
    "VOYAGE_CONTEXT_4",
    "DATABASE_URL",
    "REDIS_URL",
    "JWT_SECRET",
    "LLM_KEY_ENCRYPTION_SECRET",
    "FIREBASE_SERVICE_ACCOUNT_JSON",
    "SMTP_PASSWORD",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "TAVILY_API_KEY",
    "MSG91_API_KEY",
  ]
}

variable "service_secrets" {
  description = <<-EOT
    {service -> the exact secrets it may read}. THE POINT OF THIS MODULE.

    Note what each service does NOT get, because that is where the value is:

      api       reads everything user-facing. It does not read the Razorpay
                webhook secret, which only the webhook path verifies against.
      worker    runs the agents and sends email. NO Firebase key: a background
                task never authenticates a browser session.
      beat      schedules. It reads the broker and nothing else -- a scheduler
                that could read a model credential is a scheduler that could
                spend money.
      migrate   a one-shot job. The DSN, and nothing else at all.

    The GCP-phase finding was one runtime identity holding all of these. Nothing
    was misconfigured; the grant was simply wider than the need, and a wildcard
    looks identical whether it is over-broad or exactly right.
  EOT
  type        = map(list(string))
  default = {
    api = [
      "DATABASE_URL",
      "REDIS_URL",
      "JWT_SECRET",
      "OPENAI_GPT_TERRA",
      "OPENAI_GPT_LUNA",
      "VOYAGE_CONTEXT_4",
      "FIREBASE_SERVICE_ACCOUNT_JSON",
      "RAZORPAY_KEY_SECRET",
      "LLM_KEY_ENCRYPTION_SECRET",
    ]
    worker = [
      "DATABASE_URL",
      "REDIS_URL",
      "OPENAI_GPT_TERRA",
      "OPENAI_GPT_LUNA",
      "VOYAGE_CONTEXT_4",
      "SMTP_PASSWORD",
      "TAVILY_API_KEY",
      "MSG91_API_KEY",
      "LLM_KEY_ENCRYPTION_SECRET",
    ]
    beat = [
      "REDIS_URL",
    ]
    migrate = [
      "DATABASE_URL",
    ]
    webhook = [
      "DATABASE_URL",
      "RAZORPAY_WEBHOOK_SECRET",
    ]
  }
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
