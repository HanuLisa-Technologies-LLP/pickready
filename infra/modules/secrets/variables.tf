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
    # The RERANKER's credential, named after the model it unlocks (rerank-2.5),
    # the same convention as the line above. It holds the same Voyage ACCOUNT
    # key as VOYAGE_CONTEXT_4, because one account serves both /v1/embeddings
    # and /v1/rerank, and the names stay separate anyway: an absent key must
    # name the missing CAPABILITY, so "the reranker is not configured" is a
    # recorded degradation rather than an embedding outage wearing a
    # reranker's name.
    "VOYAGE_RERANK_2_5",
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
    # THE ONE SECRET IN THIS LIST NOBODY OUTSIDE THIS PLATFORM ISSUES, and
    # therefore the one whose value IS created here. See
    # `random_password.generated` in main.tf, and `generated_secret_names`
    # below for the split.
    #
    # It proves to `POST /verification/inbound-email` that the call came from
    # `readypick-inbound-email` and not from a stranger who knows a thread
    # token. Those travel by email, so they exist in every mailbox that ever
    # received or forwarded one of these threads.
    "INBOUND_WEBHOOK_SECRET",
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
      "VOYAGE_RERANK_2_5",
      "FIREBASE_SERVICE_ACCOUNT_JSON",
      "RAZORPAY_KEY_SECRET",
      # THE WEBHOOK HANDLER RUNS IN THIS SERVICE, NOT IN A "webhook" ONE.
      #
      # `RAZORPAY_WEBHOOK_SECRET` was granted only to a `webhook` entry below,
      # and no environment has ever defined a service by that name: the running
      # services are api, frontend and analysis. So the secret was created,
      # granted to a service that does not exist, and mounted on nothing, while
      # `POST /api/v1/billing/webhook` is served here by `api/billing.py`.
      #
      # That is why the missing secret was invisible. The handler used to treat
      # an absent secret as "development" and PROCESS the unsigned event, so an
      # anonymous POST could grant credits on the live site. The handler now
      # refuses outright when it is absent; this grant is what lets it verify
      # instead of refusing forever.
      "RAZORPAY_WEBHOOK_SECRET",
      "LLM_KEY_ENCRYPTION_SECRET",
      # THE BD PORTAL'S AI REACH RUNS IN THE REQUEST HANDLER, and this key is
      # why it returned nothing on the live site. The search is deliberately
      # NOT dispatched -- it is user-initiated, interactive and bounded by
      # `web_research.SEARCH_BUDGET_SECONDS` -- so the API is the process that
      # calls Tavily, and it was the one runtime identity without the key.
      #
      # It failed silently by design: an absent key is a supported state that
      # answers `status="unconfigured"` so the customer-database segment keeps
      # working. That graceful path is right, and it is exactly what made this
      # invisible. The task worker and the company-profile function have held
      # the key since the roster was written; only the caller that needed it
      # most did not.
      "TAVILY_API_KEY",
      # THE API IS THE ROUTE'S SIDE OF THE SHARED SECRET. It is mounted rather
      # than set as a plain env var because a task definition is readable by
      # anyone holding ecs:DescribeTaskDefinition, which is the exact shape
      # `test_deploy_secret_hygiene.py` refuses. The relay's own copy cannot be
      # a mount: see the inbound function in the pilot composition.
      "INBOUND_WEBHOOK_SECRET",
    ]
    "task-worker" = [
      "DATABASE_URL",
      "REDIS_URL",
      "OPENAI_GPT_TERRA",
      "OPENAI_GPT_LUNA",
      "VOYAGE_CONTEXT_4",
      "VOYAGE_RERANK_2_5",
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
      "VOYAGE_RERANK_2_5",
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

variable "generated_secret_names" {
  description = <<-EOT
    The secrets this module MINTS, rather than waits for a human to supply.

    Must be a subset of `secret_names`: the container is created there and the
    value is written here. A name in both lists gets a real value and no
    placeholder; a name in `secret_names` alone gets the sentinel and waits.

    THE TEST FOR WHETHER A SECRET BELONGS HERE is whether anybody else issues
    it. A Voyage key, a Razorpay secret and an SMTP app password all exist
    before Terraform runs and Terraform cannot know them. A value one half of
    this platform shows to the other half has no issuer, and leaving it to a
    human means the control it enables ships disabled until somebody remembers
    -- which for `INBOUND_WEBHOOK_SECRET` means a public write endpoint that
    logs `verification.inbound_unauthenticated` on every call and admits
    anybody.

    The cost is that a generated value lives in the Terraform state file. That
    is accepted here and is NOT a licence to move a vendor credential in: a
    vendor key in state is a second copy of something that already has a safer
    home, while this one would otherwise have no home at all.
  EOT
  type        = list(string)
  default     = ["INBOUND_WEBHOOK_SECRET"]
}

variable "service_secret_writers" {
  description = <<-EOT
    {service -> the exact secrets it may WRITE}. Almost always empty.

    Reading a secret and replacing it are different powers, and this module has
    only ever granted the first. One thing needs the second, and the reason it
    does is the 2026-09-11 outage: `DATABASE_URL` held a hand-copied snapshot
    of the RDS MASTER password, `manage_master_user_password` had Secrets
    Manager rotating that password on a schedule, and seven days after the
    instance was created the copy went stale and every database connection in
    the product failed at once.

    The fix is the design the `rds` module has documented from the start: the
    DSN carries a least-privileged application role whose password nothing else
    rotates. `app.scripts.provision_app_db_role` mints that password INSIDE the
    VPC and writes it here, so it is never an argument, never in a RunTask call,
    never in CloudTrail and never in a shell history. Rotating it later is the
    same script run again.

    Scoped the same way the read grant is: one service, an enumerated list of
    secret names, never a prefix. A service absent from this map can read what
    `service_secrets` allows and write nothing, which is the correct answer for
    every service except the one-shot migration task.
  EOT
  type        = map(list(string))
  default = {
    "migrate" = ["DATABASE_URL"]
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
