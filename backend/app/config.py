"""Application configuration using pydantic-settings."""

from pathlib import Path
from functools import lru_cache
from typing import Optional
import os
from pydantic_settings import BaseSettings
from pydantic import field_validator

# Canonical project root anchor (Afterhours repository root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Afterhours backend configuration loaded from environment / .env file."""

    # Supabase
    SUPABASE_URL: str = "http://localhost:54321"
    SUPABASE_ANON_KEY: str = "placeholder-anon-key"
    SUPABASE_SERVICE_ROLE_KEY: str = "placeholder-service-role-key"

    # Telegram
    TELEGRAM_BOT_TOKEN: str = "placeholder-bot-token"
    TELEGRAM_API_ID: int = 1234567
    TELEGRAM_API_HASH: str = "placeholder-api-hash"
    TELEGRAM_VAULT_CHAT_ID: int = -1001234567890
    TELEGRAM_STRING_SESSION: Optional[str] = None
    TELEGRAM_BOT_USERNAME: str = "afterhoursfmbot"

    # Frontend
    FRONTEND_URL: str = "http://localhost:3000"

    # Auth
    AUTH_SECRET: str = "afterhours-dev-secret-change-in-production-min-32-chars-long"
    DEV_AUTH_ENABLED: bool = True

    # Cache / storage (resolved to canonical absolute paths)
    AUDIO_CACHE_DIR: str = str((PROJECT_ROOT / "cache" / "audio").resolve())
    ARTWORK_CACHE_DIR: str = str((PROJECT_ROOT / "cache" / "artwork").resolve())
    MAX_AUDIO_CACHE_MB: int = 5120
    MAX_CACHE_SIZE_MB: int = 5000
    MAX_UPLOAD_SIZE_MB: int = 100

    # Vault Listener & Reconciliation
    VAULT_RECONCILIATION_INTERVAL_SECONDS: int = 300
    VAULT_AUTO_INGEST: bool = True

    # Streaming
    MAX_CONCURRENT_STREAMS: int = 20
    TRANSCODING_BITRATE: str = "256k"

    # Environment
    ENVIRONMENT: str = "development"

    @field_validator("TELEGRAM_BOT_TOKEN", mode="before")
    @classmethod
    def sanitize_telegram_bot_token(cls, v: Optional[str]) -> str:
        """Sanitize TELEGRAM_BOT_TOKEN (strip quotes/whitespace)."""
        val = (str(v) if v is not None else "").strip().strip("'\"")
        return val or "placeholder-bot-token"

    @field_validator("SUPABASE_URL", mode="before")
    @classmethod
    def sanitize_supabase_url(cls, v: Optional[str]) -> str:
        """Sanitize SUPABASE_URL (strip quotes/whitespace) and check common environment aliases."""
        val = (str(v) if v is not None else "").strip().strip("'\"")
        if not val or val == "http://localhost:54321" or val.startswith("placeholder"):
            alt = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_PROJECT_URL")
            if alt:
                return alt.strip().strip("'\"")
        return val or "http://localhost:54321"

    @field_validator("SUPABASE_ANON_KEY", mode="before")
    @classmethod
    def sanitize_supabase_anon_key(cls, v: Optional[str]) -> str:
        """Sanitize SUPABASE_ANON_KEY (strip quotes/whitespace) and check common environment aliases."""
        val = (str(v) if v is not None else "").strip().strip("'\"")
        if not val or val == "placeholder-anon-key" or val.startswith("placeholder"):
            alt = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY")
            if alt:
                return alt.strip().strip("'\"")
        return val or "placeholder-anon-key"

    @field_validator("SUPABASE_SERVICE_ROLE_KEY", mode="before")
    @classmethod
    def sanitize_supabase_service_role_key(cls, v: Optional[str]) -> str:
        """Sanitize SUPABASE_SERVICE_ROLE_KEY (strip quotes/whitespace) and check common environment aliases."""
        val = (str(v) if v is not None else "").strip().strip("'\"")
        if not val or val == "placeholder-service-role-key" or val.startswith("placeholder"):
            alt = (
                os.environ.get("SUPABASE_SERVICE_KEY")
                or os.environ.get("SUPABASE_SECRET_KEY")
                or os.environ.get("SERVICE_ROLE_KEY")
            )
            if alt:
                return alt.strip().strip("'\"")
        return val or "placeholder-service-role-key"

    @field_validator("MAX_AUDIO_CACHE_MB", mode="after")
    @classmethod
    def validate_cache_capacity(cls, v: int) -> int:
        """Validate that the configured cache capacity is positive."""
        if v <= 0:
            raise ValueError(f"MAX_AUDIO_CACHE_MB must be positive, got {v}")
        return v

    @field_validator("AUDIO_CACHE_DIR", "ARTWORK_CACHE_DIR", mode="after")
    @classmethod
    def resolve_absolute_paths(cls, v: str) -> str:
        """Ensure cache directories are always absolute paths anchored to PROJECT_ROOT."""
        p = Path(v)
        if not p.is_absolute():
            return str((PROJECT_ROOT / p).resolve())
        return str(p.resolve())

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    """Return cached settings singleton."""
    return Settings()
