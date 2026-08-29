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

    # ── LLM and embeddings (spec-doc5 Part B) ───────────────────────────────
    #
    # TWO CREDENTIALS PLATFORM-WIDE. This replaced a 21-slot roster (7 keys each
    # for Groq, Gemini and OpenRouter) that existed to route around three free
    # tiers' failure modes -- a retired model id, an exhausted prepaid balance,
    # an 8000-token-per-minute organisation pool, a withdrawn free-tier model.
    # One paid vendor removes that whole class of problem, so it removes the
    # roster with it.
    #
    # Both are mounted from AWS Secrets Manager in a deployed environment and
    # are never composed into a loggable env var, continuing the discipline
    # established when DATABASE_URL was hardened.
    anthropic_api_key: str = ""
    voyage_api_key: str = ""

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

    # Scaffolding only. Legal retention, data-request workflow and review remain
    # unresolved, so production must keep this disabled.
    proctoring_enabled: bool = False

    # Payments  -  Razorpay Subscriptions. The Key ID is public (Checkout needs it
    # in the browser and reads it from GET /billing/config); the Key Secret and
    # the webhook secret are server-side only and never reach a response body,
    # a log line, or the frontend bundle.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

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
