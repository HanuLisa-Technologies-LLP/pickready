"""Application settings, sourced from environment variables (see /.env.example)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://pickready:pickready@localhost:5432/pickready"
    postgres_rls_app_role: str = "pickready_app"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 7
    firebase_service_account_json: str = ""

    # OTP
    otp_ttl_minutes: int = 5
    otp_max_attempts: int = 5
    otp_cooldown_minutes: int = 15

    # LLM provider keys (3 each; router picks/falls back per ESD §8.4)
    groq_api_key_1: str = ""
    groq_api_key_2: str = ""
    groq_api_key_3: str = ""
    gemini_api_key_1: str = ""
    gemini_api_key_2: str = ""
    gemini_api_key_3: str = ""
    openrouter_api_key_1: str = ""
    openrouter_api_key_2: str = ""
    openrouter_api_key_3: str = ""
    llm_key_encryption_secret: str = ""

    # Embeddings
    bge_m3_endpoint: str = ""

    # Email (provider-agnostic SMTP) / SMS (MSG91)
    #
    # Outbound email now goes over SMTP from the backend (claude.md rule 5, as
    # of 2026-07-24 — replaces the Mailtrap HTTP API). Configured entirely by
    # env, so the same code works with Mailtrap SMTP, Gmail SMTP (app password),
    # or any provider. All SMTP_* values are read from the environment (.env) —
    # never hardcoded.
    #   * STARTTLS (the common case) → smtp_port=587, smtp_starttls=True.
    #   * Implicit TLS/SSL → smtp_port=465, smtp_ssl=True, smtp_starttls=False.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "noreply@pickready.app"
    smtp_from_name: str = "PickReady"
    smtp_starttls: bool = True
    smtp_ssl: bool = False

    # Legacy Mailtrap HTTP-API settings — retained so services/mailtrap_service.py
    # still imports cleanly (kept for its shared taxonomy), but the email task no
    # longer calls it. Not part of the delivery preflight anymore.
    mailtrap_api_token: str = ""
    mailtrap_sender_email: str = "noreply@pickready.app"
    mailtrap_sender_name: str = "PickReady"
    mailtrap_api_host: str = "send.api.mailtrap.io"
    mailtrap_inbox_id: str = ""  # when set → use sandbox host + /api/send/<id>
    msg91_api_key: str = ""
    msg91_sender_id: str = "PCKRDY"

    # File storage
    cloudinary_url: str = ""

    # App
    environment: str = "development"
    frontend_url: str = "http://localhost:3000"

    # Platform Owner — the ONLY identity permitted to hold the owner
    # (super_admin) role. Enforced in the API layer, not just seed/UI.
    owner_email: str = "manjuchro@gmail.com"

    # NOTE: settings.smtp_from_email is the default/fallback From used until a
    # tenant's own domain is SPF/DKIM-verified (an unverified From is rejected
    # or bounces by the receiving side / SMTP relay).

    # Outbound-delivery retry policy (email + SMS). Transient failures (429 /
    # 5xx / network) retry with EXPONENTIAL backoff up to this many attempts;
    # permanent failures (unverified domain, invalid recipient, bad key) never
    # retry. See app/services/sms_service.py for the failure taxonomy.
    delivery_max_retries: int = 2  # 1 initial attempt + 2 retries = 3 total

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def missing_delivery_keys(self) -> list[str]:
        """Names of unset outbound-delivery credentials (for startup preflight).

        SMTP (host/user/password) replaced the Mailtrap token as the email
        credential set; MSG91 remains the SMS credential set.
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
    — local dev without SMTP/MSG91 keys has to remain possible (the sprint
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
            "delivery.preflight MISSING outbound credentials: %s — emails/SMS "
            "using these will fail. Set them in the environment. env=%s",
            ", ".join(missing),
            settings.environment,
        )
    else:
        logging.getLogger(__name__).info(
            "delivery.preflight ok — SMTP + MSG91 credentials present"
        )
    return missing


@lru_cache
def get_settings() -> Settings:
    return Settings()
