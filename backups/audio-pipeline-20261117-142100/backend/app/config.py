"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional


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

    # Auth
    AUTH_SECRET: str = "afterhours-dev-secret-change-in-production-min-32-chars-long"
    DEV_AUTH_ENABLED: bool = True

    # Cache / storage
    AUDIO_CACHE_DIR: str = "./cache/audio"
    ARTWORK_CACHE_DIR: str = "./cache/artwork"
    MAX_CACHE_SIZE_MB: int = 5000
    MAX_UPLOAD_SIZE_MB: int = 100

    # Streaming
    MAX_CONCURRENT_STREAMS: int = 20
    TRANSCODING_BITRATE: str = "256k"

    # Environment
    ENVIRONMENT: str = "development"

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
