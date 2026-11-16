"""Supabase client wrapper and data access layer."""

import os
from typing import Optional, Dict, Any, List
from supabase import create_client, Client

from app.config import get_settings


class SupabaseService:
    """Manages Supabase clients and queries."""

    def __init__(self):
        self.settings = get_settings()
        self.client: Optional[Client] = None
        self.admin_client: Optional[Client] = None
        self._is_live = False

        if (
            self.settings.SUPABASE_URL
            and not self.settings.SUPABASE_URL.startswith("placeholder")
            and self.settings.SUPABASE_ANON_KEY
            and not self.settings.SUPABASE_ANON_KEY.startswith("placeholder")
        ):
            try:
                self.client = create_client(self.settings.SUPABASE_URL, self.settings.SUPABASE_ANON_KEY)
                self.admin_client = create_client(self.settings.SUPABASE_URL, self.settings.SUPABASE_SERVICE_ROLE_KEY)
                self._is_live = True
            except Exception:
                self._is_live = False

    def get_client(self) -> Optional[Client]:
        """Return anon client."""
        return self.client

    def get_admin(self) -> Optional[Client]:
        """Return admin / service role client."""
        return self.admin_client

    @property
    def is_live(self) -> bool:
        """Check if connected to live Supabase."""
        return self._is_live


supabase_service = SupabaseService()
