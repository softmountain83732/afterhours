"""Supabase client wrapper and data access layer."""

import logging
import os
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse
from supabase import create_client, Client

from app.config import get_settings

logger = logging.getLogger("supabase_client")


class SupabaseService:
    """Manages Supabase clients and queries."""

    def __init__(self):
        self.settings = get_settings()
        self.client: Optional[Client] = None
        self.admin_client: Optional[Client] = None
        self._is_live = False
        self.offline_reason: str = "Uninitialized"

        self.initialize()

    def initialize(self):
        """
        Initialize Supabase client and service-role admin client with sanitized credentials.
        Emits safe diagnostic logs without exposing secret keys.
        """
        raw_url = (self.settings.SUPABASE_URL or "").strip().strip("'\"")
        raw_anon = (self.settings.SUPABASE_ANON_KEY or "").strip().strip("'\"")
        raw_service = (self.settings.SUPABASE_SERVICE_ROLE_KEY or "").strip().strip("'\"")

        # Extract hostname safely for diagnostics
        url_host = "invalid"
        try:
            parsed = urlparse(raw_url)
            url_host = parsed.netloc or parsed.path or "unknown"
        except Exception:
            pass

        has_url = bool(raw_url) and not raw_url.startswith("placeholder")
        is_localhost = raw_url.startswith("http://localhost") or raw_url.startswith("http://127.0.0.1")
        has_anon = bool(raw_anon) and not raw_anon.startswith("placeholder")
        has_service = bool(raw_service) and not raw_service.startswith("placeholder")

        # In production environments (or on Heroku/Dyno), localhost URLs indicate unconfigured Supabase
        is_production = self.settings.ENVIRONMENT == "production" or bool(os.environ.get("DYNO"))
        if is_production and is_localhost:
            self._is_live = False
            self.offline_reason = f"SUPABASE_URL points to localhost ({url_host}) in production environment"
            logger.warning(
                "Supabase initialization skipped: %s. Real database queries cannot be executed.",
                self.offline_reason,
            )
            return

        if not has_url:
            self._is_live = False
            self.offline_reason = "SUPABASE_URL not configured or placeholder"
            logger.warning("Supabase initialization skipped: %s.", self.offline_reason)
            return

        if not has_anon and not has_service:
            self._is_live = False
            self.offline_reason = "Neither SUPABASE_ANON_KEY nor SUPABASE_SERVICE_ROLE_KEY is configured"
            logger.warning("Supabase initialization skipped: %s.", self.offline_reason)
            return

        logger.info(
            "Initializing Supabase clients: host=%s, has_anon=%s (len=%d), has_service_role=%s (len=%d)...",
            url_host,
            has_anon,
            len(raw_anon) if has_anon else 0,
            has_service,
            len(raw_service) if has_service else 0,
        )

        try:
            # Initialize anon client if anon key is available
            if has_anon:
                self.client = create_client(raw_url, raw_anon)
                logger.info("Supabase anon client created successfully.")
            else:
                logger.warning("Supabase anon key not configured; client will be None.")

            # Initialize service-role admin client
            if has_service:
                self.admin_client = create_client(raw_url, raw_service)
                logger.info("Supabase service-role admin client created successfully.")
            else:
                logger.warning(
                    "SUPABASE_SERVICE_ROLE_KEY not configured or placeholder. "
                    "Admin client falling back to anon client; operations requiring RLS bypass may fail."
                )
                self.admin_client = self.client

            self._is_live = (self.admin_client is not None or self.client is not None)
            self.offline_reason = "Connected" if self._is_live else "Client creation failed"
            logger.info("Supabase service initialized: is_live=%s, host=%s", self._is_live, url_host)
        except Exception as exc:
            self._is_live = False
            self.offline_reason = f"Client initialization error ({type(exc).__name__}: {exc})"
            logger.error("Failed to initialize Supabase client: %s", self.offline_reason)

    def get_client(self) -> Optional[Client]:
        """Return anon client, falling back to admin client if only admin is configured."""
        return self.client or self.admin_client

    def get_admin(self) -> Optional[Client]:
        """Return admin / service role client, falling back to anon client if service role not provided."""
        return self.admin_client or self.client

    @property
    def is_live(self) -> bool:
        """Check if connected to live Supabase."""
        return self._is_live


supabase_service = SupabaseService()
