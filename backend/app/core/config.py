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

    # Email / SMS
    resend_api_key: str = ""
    msg91_api_key: str = ""
    msg91_sender_id: str = "PCKRDY"

    # File storage
    cloudinary_url: str = ""

    # App
    environment: str = "development"
    frontend_url: str = "http://localhost:3000"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
