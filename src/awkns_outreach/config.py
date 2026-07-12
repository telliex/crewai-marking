"""Central settings for the outreach service, loaded from the environment.

One source of truth for every tunable — API keys, the database URL, sender
identity, and the anti-spam gate. Import `settings` anywhere; it is a singleton
built once at process start.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- External services ---
    apollo_api_key: str = Field(default="", alias="APOLLO_API_KEY")
    resend_api_key: str = Field(default="", alias="RESEND_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    serper_api_key: str = Field(default="", alias="SERPER_API_KEY")

    # --- Database ---
    # e.g. postgresql+psycopg://user:pass@localhost:5432/outreach
    database_url: str = Field(
        default="postgresql+psycopg://localhost:5432/outreach",
        alias="DATABASE_URL",
    )

    # --- Sender identity / compliance (global defaults; a campaign may override) ---
    outreach_from: str = Field(default="noreply@localhost", alias="OUTREACH_FROM")
    outreach_from_name: str = Field(default="Steven", alias="OUTREACH_FROM_NAME")
    outreach_reply_to: str = Field(default="", alias="OUTREACH_REPLY_TO")
    outreach_sender_name: str = Field(default="Steven Wu", alias="OUTREACH_SENDER_NAME")
    outreach_company: str = Field(default="Awkns", alias="OUTREACH_COMPANY")
    # REQUIRED for real sends (CAN-SPAM / JP / KR / EU). Empty blocks live sending.
    outreach_postal_address: str = Field(default="", alias="OUTREACH_POSTAL_ADDRESS")
    outreach_unsubscribe_mailto: str = Field(
        default="", alias="OUTREACH_UNSUBSCRIBE_MAILTO"
    )
    # HMAC secret for signing unsubscribe tokens. MUST be set in production.
    outreach_unsubscribe_secret: str = Field(
        default="dev-insecure-secret", alias="OUTREACH_UNSUBSCRIBE_SECRET"
    )

    # --- Web ---
    app_base_url: str = Field(default="http://localhost:8000", alias="APP_BASE_URL")
    admin_password: str = Field(default="", alias="ADMIN_PASSWORD")
    # Shared secret Resend signs webhooks with (svix). Empty disables verification.
    resend_webhook_secret: str = Field(default="", alias="RESEND_WEBHOOK_SECRET")

    # --- Writer ---
    crew_model: str = Field(default="anthropic/claude-sonnet-4-5", alias="CREW_MODEL")
    # Bare Anthropic SDK model id (NOT the LiteLLM-style "anthropic/..." prefix
    # that crew_model uses) — this one goes straight to anthropic.Anthropic().
    tier_model: str = Field(default="claude-haiku-4-5", alias="TIER_MODEL")

    # --- Gmail mailbox (OAuth) --- Google Cloud Console OAuth Client ID (Web
    # application); redirect URI is derived from app_base_url, no separate env var.
    google_client_id: str = Field(default="", alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", alias="GOOGLE_CLIENT_SECRET")

    @property
    def reply_to(self) -> str:
        return self.outreach_reply_to or self.outreach_from


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
