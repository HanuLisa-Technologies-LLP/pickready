"""Application settings, sourced from environment variables (see /.env.example)."""
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://pickready:pickready@localhost:5432/pickready"
    postgres_rls_app_role: str = "pickready_app"

    # Connection pool (app/core/db.get_engine). The SQLAlchemy defaults (5 + 10)
    # are small enough that a few concurrent tabs queue for a connection and
    # every request in the queue reads as "slow".
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 30
    db_pool_recycle_seconds: int = 1800

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    technical_review_reminder_hours: int = 48

    # Auth
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 7
    firebase_service_account_json: str = ""

    # Auth-cookie attributes (app/api/deps.py is the single writer).
    #
    # "strict" is correct whenever the browser page and the API are same-site,
    # which covers local dev (localhost:3000 -> localhost:8000; cookies ignore
    # the port) and a production deployment on one registrable domain
    # (app.example.com + api.example.com). It is WRONG when the two live on
    # different registrable domains (a Vercel frontend calling a Railway API):
    # the browser then withholds the cookie on every call and the user can never
    # stay signed in. That deployment must set COOKIE_SAMESITE=none, which the
    # validator below forces to be paired with a secure (https) cookie.
    #
    # COOKIE_DOMAIN is normally empty (host-only cookie, the safest default).
    # Set it to ".example.com" only when the frontend and API are on different
    # subdomains of one domain.
    cookie_samesite: str = "strict"
    cookie_domain: str = ""

    # OTP
    otp_ttl_minutes: int = 5
    otp_max_attempts: int = 5
    otp_cooldown_minutes: int = 15

    # ── LLM and embeddings ──────────────────────────────────────────────────
    #
    # THREE CREDENTIALS PLATFORM-WIDE, ONE PER MODEL. This replaced a 21-slot
    # roster (7 keys each for Groq, Gemini and OpenRouter) that existed to route
    # around three free tiers' failure modes -- a retired model id, an exhausted
    # prepaid balance, an 8000-token-per-minute organisation pool, a withdrawn
    # free-tier model. One paid vendor removes that whole class of problem, so
    # it removes the roster with it.
    #
    # THE MODEL VENDOR CHANGED ON 2026-08-31, from Anthropic to OpenAI, by owner
    # decision. `ANTHROPIC_API_KEY` is gone rather than deprecated: a retained
    # credential is a credential something eventually reads. The embedding
    # VENDOR did not change; the variable its key is read from did.
    #
    # TWO KEYS FOR ONE MODEL VENDOR is unusual and is what the owner has: the
    # reasoning tier and the extraction tier are billed separately. Which model
    # is called with which is DATA in `config/llm_providers.SETTINGS_ATTR_FOR_MODEL`,
    # never a branch in the router, and an absent key for the model being called
    # raises rather than falling back to the other one.
    #
    # All three are mounted from AWS Secrets Manager in a deployed environment
    # and are never composed into a loggable env var, continuing the discipline
    # established when DATABASE_URL was hardened.
    openai_gpt_terra: str = ""
    openai_gpt_luna: str = ""

    # THE EMBEDDING CREDENTIAL IS NAMED AFTER THE MODEL IT UNLOCKS:
    # `VOYAGE_CONTEXT_4` for `voyage-4`. Same convention as the two
    # model keys above, and it replaces `VOYAGE_API_KEY` outright rather than
    # sitting beside it as an alias. One name per thing.
    #
    # READING THE WRONG NAME HERE WOULD NOT RAISE, which is why
    # `tests/test_embeddings.py` pins this exact variable as a literal.
    # `services/embeddings` falls back to deterministic pseudo-random unit
    # vectors when the key is absent, so a mistyped name leaves every retrieval
    # returning meaningless vectors of the right width, with no exception, no
    # log line and no empty result to notice.
    voyage_context_4: str = ""

    # Retained, unread by the router. `llm_provider_keys` still holds encrypted
    # rows for the three retired vendors and this is what decrypts them; a
    # rollback of the consolidation needs the rows readable rather than
    # restored from a backup.
    llm_key_encryption_secret: str = ""

    # Embedding output width. Pinned to 1024 because `profiles.embedding`,
    # `jobs.embedding` and `context_chunks.embedding` are vector(1024) columns
    # already holding vectors. Changing this is a re-embed of every row, not a
    # config change.
    embedding_dimensions: int = 1024

    # Advanced web search for the BD Portal's AI Reach agent
    # (services/web_research.py). OPTIONAL: with no key the "from the internet"
    # segment returns an empty list with status "unconfigured" and a plain
    # message, while "similar to our customers" keeps working.
    tavily_api_key: str = ""

    # Gmail SMTP only. Values come from the environment and are validated below.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "noreply@pickready.app"
    smtp_from_name: str = "ReadyPick"
    smtp_starttls: bool = True
    smtp_ssl: bool = False

    msg91_api_key: str = ""
    msg91_sender_id: str = "PCKRDY"

    # ── Private file storage (S3) ───────────────────────────────────────────
    #
    # Durable values stored in the database are s3:// object references; a raw
    # bucket URL is never returned to a browser. Access always passes through an
    # authenticated, tenant-scoped, capability-checked endpoint rather than a
    # presigned link, because a presigned URL is a bearer token that leaves no
    # audit trail once it has been copied out of a page.
    #
    # No access key or secret lives here. In a deployed environment boto3
    # resolves the ECS task role, which is scoped to exactly this bucket by the
    # per-service IAM policy in `infra/modules/secrets`. A long-lived key in an
    # env var is precisely what that scoping exists to avoid.
    s3_bucket: str = ""
    aws_region: str = "ap-south-1"
    #: Localstack / MinIO only. None in every real environment, where boto3
    #: resolves the real regional endpoint.
    s3_endpoint_url: str = ""
    resume_signed_url_ttl_seconds: int = 300

    # ── Proctoring (proctoring-spec-doc.md) ─────────────────────────────────
    #
    # EVERY THRESHOLD THE PROCTORING SYSTEM USES IS HERE, AND NOWHERE ELSE.
    # The specification's "defaults" are starting configuration, not
    # constants, so each one is a setting an operator can move without a code
    # change. `services/proctoring/config.py` reads them into one frozen
    # object and serves the browser-side subset to the client at session
    # start, which is what keeps the client and the server working from the
    # same numbers. No module in the proctoring pipeline may carry a literal.
    #
    # Proctoring is MANDATORY (principle P4). There is no enable flag: a
    # candidate who declines the consent screen does not take the assessment.
    # The one feature flag below governs the AI-text detector only, because
    # that signal is documented as unreliable and ships disabled.
    proctoring_max_warnings: int = 3
    # Object detection (section 3.1, 4.2).
    proctoring_object_confidence_threshold: float = 0.65
    proctoring_object_consecutive_frames: int = 3
    proctoring_object_cooldown_seconds: int = 30
    proctoring_second_person_cooldown_seconds: int = 30
    # Face identity (section 3.3, 4.1).
    proctoring_face_distance_threshold: float = 0.6
    proctoring_identity_check_interval_seconds: int = 30
    proctoring_identity_consecutive_mismatches: int = 2
    # Camera obstruction versus face absence (section 4.1, 4.2, 4.6).
    proctoring_obstruction_seconds: int = 60
    #: Per-frame greyscale standard deviation below which a frame with no face
    #: is an obstruction (covered lens, tape, closed shutter) rather than an
    #: absence. Computed in the browser from the frame it then discards.
    proctoring_obstruction_variance_threshold: float = 12.0
    proctoring_face_absent_moderate_seconds: int = 20
    proctoring_face_absent_moderate_cooldown_seconds: int = 60
    proctoring_face_absent_extended_seconds: int = 90
    # Browser lockdown and focus (section 4.2).
    proctoring_focus_loss_ignore_under_seconds: float = 2.0
    proctoring_display_check_interval_seconds: int = 60
    # Audio (section 3.4, 4.2).
    proctoring_audio_chunk_seconds: int = 15
    proctoring_audio_max_chunk_bytes: int = 2 * 1024 * 1024
    proctoring_second_voice_consecutive_chunks: int = 2
    #: The analysis service (speaker diarization, AI-text detection). Empty
    #: means audio analysis is UNAVAILABLE, which the report states plainly;
    #: it is never silently treated as "no second voice".
    proctoring_analysis_service_url: str = ""
    proctoring_analysis_timeout_seconds: float = 20.0
    # Anti-tamper (section 9).
    proctoring_heartbeat_interval_seconds: int = 10
    proctoring_heartbeat_gap_seconds: int = 30
    proctoring_integrity_failure_termination_seconds: int = 60
    proctoring_camera_recovery_seconds: int = 60
    # In-browser inference performance (section 3.6).
    proctoring_sampling_fps_normal: int = 2
    proctoring_sampling_fps_confirming: int = 6
    proctoring_confirming_window_seconds: int = 5
    proctoring_sampling_fps_degraded: int = 1
    proctoring_low_light_luminance_threshold: float = 40.0
    proctoring_low_light_cooldown_seconds: int = 300
    # Behavioural capture (section 4.5). Thresholds compare the candidate
    # against THEIR OWN baseline from their first answers, never a population.
    proctoring_baseline_answers: int = 2
    proctoring_fast_entry_multiplier: float = 3.5
    proctoring_fast_entry_sustained_seconds: int = 10
    proctoring_uniform_span_chars: int = 200
    proctoring_uniform_max_corrections: int = 5
    proctoring_uniform_max_pause_seconds: float = 1.0
    proctoring_low_ratio_min_length: int = 150
    proctoring_low_ratio_threshold: float = 0.85
    proctoring_pause_gap_seconds: float = 2.0
    proctoring_burst_window_seconds: int = 5
    proctoring_mouse_sample_hz: int = 10
    proctoring_max_keystroke_samples: int = 20_000
    proctoring_event_batch_max: int = 200
    # AI-generated-text detection (section 3.5). INFORMATIONAL ONLY and
    # disabled by default: the detectors are unreliable against current
    # models, and the signal never contributes to a warning, a termination, a
    # score or a ranking whatever this flag says.
    proctoring_ai_text_detection_enabled: bool = False
    proctoring_ai_text_threshold: float = 0.9
    proctoring_ai_text_min_chars: int = 200
    #: Event retention (section 5). ZERO means "the platform's existing
    #: candidate-data policy", which is deletion by cascade when the candidate
    #: or application is deleted; the platform has no time-based purge and
    #: this setting does not invent one. A positive value enables the hourly
    #: purge of events older than that many days. Owner decision.
    proctoring_event_retention_days: int = 0

    # ── Assessment question formats (assessment-spec-doc.md) ────────────────
    #
    # Composition is enforced in code, not suggested in a prompt: evidence
    # questions must be the majority of the assessment's time and weight, the
    # supporting formats the minority, and the whole thing must fit the
    # role's duration. These are the bounds. `services/assessment_formats/
    # config.py` reads them into one object; nothing else carries a literal.
    #: Evidence-based questions' minimum share of total weight AND of total
    #: time allocation. Above one half by definition of "majority", with a
    #: margin so a rounding effect cannot tip a valid assessment over.
    assessment_evidence_min_share: float = 0.55
    #: The supporting formats' (MCQ, fill-blank, coding) maximum share of the
    #: QUESTION COUNT, by seniority. Senior roles skew further toward
    #: evidence and away from recall-style questions.
    assessment_supporting_max_share: float = 0.25
    assessment_supporting_max_share_senior: float = 0.15
    #: The assessment's total suggested duration per grade, in minutes. The
    #: sum of every question's time allocation must fit inside it.
    assessment_duration_minutes_non_managerial: int = 100
    assessment_duration_minutes_managerial: int = 85
    assessment_duration_minutes_leadership: int = 70
    assessment_duration_minutes_cxo: int = 50
    #: Suggested time per question, by format, in seconds.
    assessment_time_evidence_seconds: int = 240
    assessment_time_short_answer_seconds: int = 180
    assessment_time_mcq_single_seconds: int = 60
    assessment_time_mcq_multi_seconds: int = 90
    assessment_time_fill_blank_seconds: int = 60
    assessment_time_coding_seconds: int = 600
    #: INTERNAL weight per format, within a matrix item. What makes evidence
    #: dominance structural rather than stated.
    assessment_weight_evidence: float = 1.0
    assessment_weight_short_answer: float = 1.0
    assessment_weight_mcq_single: float = 0.4
    assessment_weight_mcq_multi: float = 0.5
    assessment_weight_fill_blank: float = 0.4
    assessment_weight_coding: float = 0.8
    #: How many times the composer may regenerate a mix that fails validation
    #: before it falls back to an all-evidence allocation for the supporting
    #: slots, which is always valid.
    assessment_composition_attempts: int = 3
    #: The fewest words an AI evaluation's reasoning may carry. A bare verdict
    #: with a sentence attached is not a reasoning a recruiter can act on.
    assessment_evaluation_min_reasoning_words: int = 40
    #: The shortest quotable resume item an evidence question may anchor to.
    #: Below this an "anchor" is a single word, which anchors nothing.
    assessment_anchor_min_chars: int = 12
    #: The fewest words a distractor's misconception rationale may carry
    #: before the option counts as a real misconception rather than filler.
    assessment_misconception_min_words: int = 4

    # ── Project Evidence Intelligence limits ────────────────────────────────
    #
    # Candidate project submissions are UNTRUSTED input processed into derived
    # evidence; the original artifact is temporary by product decision (the
    # 2026-09-01 Project Evidence master brief) and is deleted once evidence is
    # persisted. These are the safe processing ceilings, DATA here rather than
    # literals in the pipeline so an operator can widen them without a deploy
    # of new code paths. Sized against the resume path's own ceilings (10 MB a
    # file) and the worker's 600 s soft time limit: the whole pipeline for one
    # project must finish comfortably inside one task slot.
    project_max_projects_per_candidate: int = 10
    project_max_files: int = 20
    project_max_file_bytes: int = 25 * 1024 * 1024
    project_max_total_bytes: int = 100 * 1024 * 1024
    #: Archive extraction guards (zip bombs, nesting, entry floods).
    project_max_archive_depth: int = 2
    project_max_archive_entries: int = 2000
    project_max_extracted_bytes: int = 200 * 1024 * 1024
    #: A compressed entry claiming to inflate past this ratio is refused as a
    #: decompression bomb before a single byte is extracted.
    project_max_compression_ratio: int = 120
    #: Per-file ceiling on text promoted into deterministic parsing.
    project_max_text_chars_per_file: int = 60_000
    #: Evidence reduction: the most units one project may persist, and the most
    #: characters of evidence-pack context one AI reasoning call may receive.
    project_max_evidence_units: int = 120
    project_max_ai_context_chars: int = 24_000
    #: Public-repository ingestion caps: how many meaningful files are fetched
    #: after tree classification, and the largest single file fetched.
    project_repo_max_files: int = 40
    project_repo_max_file_bytes: int = 512_000
    #: OPTIONAL. Raises the public GitHub API rate limit; grants no private
    #: access and is never required. Public repositories only, by product
    #: decision: no private-repository OAuth or token intake exists.
    github_api_token: str = ""

    # Payments  -  Razorpay Subscriptions. The Key ID is public (Checkout needs it
    # in the browser and reads it from GET /billing/config); the Key Secret and
    # the webhook secret are server-side only and never reach a response body,
    # a log line, or the frontend bundle.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    # ReadyPick's own GST registration number, printed on every credit-pack
    # invoice (Master Directive Part 5 §5.2). Configuration, not code: it is a
    # legal identifier that changes with registration, never with a release.
    readypick_gstin: str = ""

    # App
    environment: str = "development"
    frontend_url: str = "http://localhost:3000"

    # Platform Owner  -  the ONLY identity permitted to hold the owner
    # (super_admin) role. Enforced in the API layer, not just seed/UI.
    owner_email: str = "manjuchro@gmail.com"

    # Gmail's authenticated mailbox is always the From address.

    # Outbound-delivery retry cap. Email retries transient failures after a
    # fixed 60-second delay; SMS retains exponential backoff. Permanent
    # failures never retry.
    delivery_max_retries: int = 3  # initial attempt + up to 3 retries

    @model_validator(mode="after")
    def validate_gmail_smtp(self) -> "Settings":
        configured = bool(self.smtp_host or self.smtp_user or self.smtp_password)
        if not configured:
            return self
        if self.smtp_host != "smtp.gmail.com":
            raise ValueError("SMTP_HOST must be smtp.gmail.com")
        if self.smtp_port != 587 or not self.smtp_starttls or self.smtp_ssl:
            raise ValueError(
                "Gmail SMTP requires port 587 with STARTTLS enabled and SSL disabled"
            )
        if self.smtp_user and not self.smtp_user.lower().endswith("@gmail.com"):
            raise ValueError("SMTP_USER must be a Gmail address")
        if self.smtp_user and self.smtp_from_email.lower() != self.smtp_user.lower():
            raise ValueError("SMTP_FROM_EMAIL must match SMTP_USER")
        return self

    @model_validator(mode="after")
    def validate_cookie_policy(self) -> "Settings":
        value = (self.cookie_samesite or "").strip().lower()
        if value not in {"strict", "lax", "none"}:
            raise ValueError("COOKIE_SAMESITE must be strict, lax or none")
        object.__setattr__(self, "cookie_samesite", value)
        if value == "none" and self.environment != "production":
            # SameSite=None is only honoured on a Secure (https) cookie, and
            # `secure` is tied to the production flag. Refuse the combination
            # rather than shipping a cookie every browser silently drops.
            raise ValueError(
                "COOKIE_SAMESITE=none requires ENVIRONMENT=production so the "
                "cookie is marked Secure"
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def missing_delivery_keys(self) -> list[str]:
        """Names of unset outbound-delivery credentials (for startup preflight).

        Gmail SMTP (host/user/password) is the email credential set; MSG91
        remains the SMS credential set.
        """
        checks = {
            "SMTP_HOST": self.smtp_host,
            "SMTP_USER": self.smtp_user,
            "SMTP_PASSWORD": self.smtp_password,
            "MSG91_API_KEY": self.msg91_api_key,
            "MSG91_SENDER_ID": self.msg91_sender_id,
        }
        return [name for name, value in checks.items() if not value]


def preflight_delivery_config() -> list[str]:
    """Log a loud WARNING for any missing email/SMS credential at startup.

    ASSUMPTION: a missing key must NOT hard-crash the container in development
     -  local dev without SMTP/MSG91 keys has to remain possible (the sprint
    brief only requires that a missing key not fail *silently*). In production
    the same warning is emitted; enforcement/alerting on it is an ops concern,
    not a process-exit here. Returns the list of missing key names so callers
    (or tests) can assert on it.
    """
    import logging

    settings = get_settings()
    missing = settings.missing_delivery_keys()
    if missing:
        logging.getLogger(__name__).warning(
            "delivery.preflight MISSING outbound credentials: %s  -  emails/SMS "
            "using these will fail. Set them in the environment. env=%s",
            ", ".join(missing),
            settings.environment,
        )
    else:
        logging.getLogger(__name__).info(
            "delivery.preflight ok  -  SMTP + MSG91 credentials present"
        )
    return missing


@lru_cache
def get_settings() -> Settings:
    return Settings()
