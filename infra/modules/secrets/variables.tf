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
    "HUGGINGFACE_TOKEN",
  ]
}

variable "service_secrets" {
  description = <<-EOT
    {service -> the exact secrets it may read}. THE POINT OF THIS MODULE.

    Note what each consumer does NOT get, because that is where the value is:

      api          reads everything a request handler needs. It does not read
                   the Razorpay webhook secret, which only the webhook path
                   verifies against, and it does not read the Hugging Face
                   token.
      task-worker  the generic short-work Lambda: delivery, resume parsing and
                   the reconciliation sweeps. NO Firebase key, because a
                   background task never authenticates a browser session.
      agent        the on-demand Fargate task that runs the long AI work.
      jd-gen       the two request/response agent functions. Each reads the two
      company-     model credentials and the embedding key. Neither reads the
      profile      SMTP password, the payment secrets or the Firebase key: they
                   write a draft and send nothing.
      trigger      the function that calls ecs:RunTask reads NO SECRET AT ALL
                   and so has NO ENTRY HERE. An empty list would be worse than
                   an absence: this module emits one IAM policy per entry, and
                   a policy whose statement has an empty resource list is one
                   AWS refuses. It is the only thing in the account holding
                   iam:PassRole, which is why its blast radius is kept at
                   exactly that one permission and nothing else.
      migrate      a one-shot task. The DSN, and nothing else at all.
      analysis     the proctoring analysis service. The Hugging Face token that
                   unlocks the gated diarization models, and nothing else: it
                   holds no DSN, no cache endpoint and no model-provider key,
                   because the only thing it is handed is fifteen seconds of
                   audio and the only thing it answers is a speaker count.

    The GCP-phase finding was one runtime identity holding all of these. Nothing
    was misconfigured; the grant was simply wider than the need, and a wildcard
    looks identical whether it is over-broad or exactly right.
  EOT
  type        = map(list(string))
  default = {
    "api" = [
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
    "task-worker" = [
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
    "agent" = [
      "DATABASE_URL",
      "REDIS_URL",
      "OPENAI_GPT_TERRA",
      "OPENAI_GPT_LUNA",
      "VOYAGE_CONTEXT_4",
      "LLM_KEY_ENCRYPTION_SECRET",
    ]
    "jd-gen" = [
      "DATABASE_URL",
      "OPENAI_GPT_TERRA",
      "OPENAI_GPT_LUNA",
      "LLM_KEY_ENCRYPTION_SECRET",
    ]
    "company-profile" = [
      "DATABASE_URL",
      "OPENAI_GPT_TERRA",
      "OPENAI_GPT_LUNA",
      "TAVILY_API_KEY",
      "LLM_KEY_ENCRYPTION_SECRET",
    ]
    "migrate" = [
      "DATABASE_URL",
    ]
    "webhook" = [
      "DATABASE_URL",
      "RAZORPAY_WEBHOOK_SECRET",
    ]
    "analysis" = [
      "HUGGINGFACE_TOKEN",
    ]
  }
}

variable "placeholder_value" {
  description = <<-EOT
    What every secret holds until a human puts the real value in.

    It exists because Secrets Manager refuses an empty `SecretString` and ECS
    refuses to start a task whose secret has no version at all. It is not a
    credential and cannot authenticate to anything.

    `app.core.config` normalises this exact string back to "" before any code
    reads it, so the paths that run are the ones that run when the variable is
    unset. Changing it here means changing `config.PLACEHOLDER_SECRET` with it,
    and `backend/tests/test_placeholder_secret.py` fails if they disagree.
  EOT
  type        = string
  default     = "PLACEHOLDER_NOT_CONFIGURED"
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
