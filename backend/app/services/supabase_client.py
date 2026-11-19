"""Supabase client wrapper and data access layer."""

import logging
import os
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse
from supabase import create_client, Client

from app.config import get_settings

logger = logging.getLogger("supabase_client")


def classify_key_format(key: Optional[str]) -> Dict[str, Any]:
    """
    Safely classify key format and extract diagnostic info without exposing secret material.

    Returns:
        dict with:
            - type: "jwt" | "sb_publishable" | "sb_secret" | "placeholder" | "empty" | "other"
            - prefix: masked prefix (e.g. "sb_publishable_****", "sb_secret_****", "ey...****", "(empty)")
            - length: integer length of the key
    """
    if not key:
        return {"type": "empty", "prefix": "(empty)", "length": 0}

    k = key.strip().strip("'\"")
    length = len(k)

    if k.startswith("placeholder"):
        return {"type": "placeholder", "prefix": "placeholder****", "length": length}
    if k.startswith("sb_publishable_"):
        return {"type": "sb_publishable", "prefix": "sb_publishable_****", "length": length}
    if k.startswith("sb_secret_"):
        return {"type": "sb_secret", "prefix": "sb_secret_****", "length": length}
    if k.startswith("ey") and k.count(".") == 2:
        return {"type": "jwt", "prefix": "ey...****", "length": length}

    prefix_str = f"{k[:4]}****" if length >= 4 else "****"
    return {"type": "other", "prefix": prefix_str, "length": length}


def get_key_source(primary_env: str, aliases: List[str], current_val: Optional[str]) -> str:
    """Determine the origin of a key: direct primary env var, alias, or default/fallback."""
    if not current_val or current_val.startswith("placeholder"):
        return "default/placeholder"

    val_clean = current_val.strip().strip("'\"")
    primary_val = (os.environ.get(primary_env) or "").strip().strip("'\"")
    if primary_val and primary_val == val_clean:
        return f"{primary_env} (direct)"

    for alias in aliases:
        alias_val = (os.environ.get(alias) or "").strip().strip("'\"")
        if alias_val and alias_val == val_clean:
            return f"{alias} (alias)"

    return "configured/settings"


def _apply_supabase_compatibility_shim(sync_client_mod=None):
    """
    Ensure compatibility with modern Supabase key formats (sb_publishable_*, sb_secret_*).
    In older supabase-py (< 2.12.0), Client.__init__ rejected non-JWT keys using a strict regex.
    This shim intercepts that check if running under older supabase-py versions.
    """
    try:
        if sync_client_mod is None:
            import supabase._sync.client as sync_client_mod
        client_cls = getattr(sync_client_mod, "SyncClient", None) or getattr(sync_client_mod, "Client", None)
        if not client_cls:
            return

        needs_patch = False
        try:
            import inspect
            src = inspect.getsource(client_cls.__init__)
            if "Check if the key is a valid JWT" in src or "Invalid API key" in src:
                needs_patch = True
        except Exception:
            pass

        if not needs_patch:
            try:
                import supabase
                ver = getattr(supabase, "__version__", "0.0.0")
                parts = [int(p) for p in ver.split(".")[:2] if p.isdigit()]
                if len(parts) >= 2 and (parts[0] < 2 or (parts[0] == 2 and parts[1] < 12)):
                    needs_patch = True
            except Exception:
                pass

        if getattr(client_cls, "_legacy_jwt_check", False):
            needs_patch = True

        if needs_patch:
            orig_init = client_cls.__init__

            def patched_init(self, supabase_url: str, supabase_key: str, *args, **kwargs):
                orig_match = sync_client_mod.re.match

                def safe_match(pattern, string, *margs, **mkwargs):
                    if pattern and str(pattern).startswith("^[A-Za-z0-9-_=]+\\."):
                        if string and (str(string).startswith("sb_publishable_") or str(string).startswith("sb_secret_")):
                            class _DummyMatch:
                                pass
                            return _DummyMatch()
                    return orig_match(pattern, string, *margs, **mkwargs)

                sync_client_mod.re.match = safe_match
                try:
                    return orig_init(self, supabase_url, supabase_key, *args, **kwargs)
                finally:
                    sync_client_mod.re.match = orig_match

            client_cls.__init__ = patched_init
            logger.info("Applied Supabase legacy key validation compatibility shim for modern keys.")
    except Exception as shim_err:
        logger.debug("Supabase compatibility shim check skipped: %s", shim_err)


_apply_supabase_compatibility_shim()


class SupabaseService:
    """Manages Supabase clients and queries."""

    def __init__(self):
        self.settings = get_settings()
        self.client: Optional[Client] = None
        self.admin_client: Optional[Client] = None
        self._is_live = False
        self.offline_reason: str = "Uninitialized"
        self.diagnostics: Dict[str, Any] = {}

        self.initialize()

    def initialize(self):
        """
        Initialize Supabase client and service-role admin client with sanitized credentials.
        Emits safe diagnostic logs without exposing secret keys.
        """
        _apply_supabase_compatibility_shim()

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

        anon_diag = classify_key_format(raw_anon)
        anon_source = get_key_source("SUPABASE_ANON_KEY", ["NEXT_PUBLIC_SUPABASE_ANON_KEY", "SUPABASE_KEY"], raw_anon)

        service_diag = classify_key_format(raw_service)
        service_source = get_key_source(
            "SUPABASE_SERVICE_ROLE_KEY",
            ["SUPABASE_SERVICE_KEY", "SUPABASE_SECRET_KEY", "SERVICE_ROLE_KEY"],
            raw_service,
        )

        url_source = get_key_source(
            "SUPABASE_URL",
            ["NEXT_PUBLIC_SUPABASE_URL", "SUPABASE_PROJECT_URL"],
            raw_url,
        )

        self.diagnostics = {
            "host": url_host,
            "url_source": url_source,
            "anon": {
                "source": anon_source,
                "type": anon_diag["type"],
                "prefix": anon_diag["prefix"],
                "length": anon_diag["length"],
            },
            "service_role": {
                "source": service_source,
                "type": service_diag["type"],
                "prefix": service_diag["prefix"],
                "length": service_diag["length"],
            },
        }

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
            "Supabase config diagnostics: host=%s (source=%s), "
            "anon_key=[source=%s, type=%s, prefix=%s, len=%d], "
            "service_role_key=[source=%s, type=%s, prefix=%s, len=%d]",
            url_host,
            url_source,
            anon_source,
            anon_diag["type"],
            anon_diag["prefix"],
            anon_diag["length"],
            service_source,
            service_diag["type"],
            service_diag["prefix"],
            service_diag["length"],
        )

        try:
            # Initialize anon client if anon key is available
            if has_anon:
                self.client = create_client(raw_url, raw_anon)
                logger.info("Supabase anon client created successfully (key_type=%s).", anon_diag["type"])
            else:
                logger.warning("Supabase anon key not configured; client will be None.")

            # Initialize service-role admin client
            if has_service:
                self.admin_client = create_client(raw_url, raw_service)
                logger.info("Supabase service-role admin client created successfully (key_type=%s).", service_diag["type"])
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
