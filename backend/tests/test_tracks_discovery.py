"""Regression tests for track discovery, Supabase live queries, diagnostic logging, and error handling."""

import os
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import Settings
from app.services.supabase_client import SupabaseService, supabase_service
from app.routers.tracks import format_track

client = TestClient(app)


def test_supabase_credential_sanitization_and_aliases():
    """Verify that Settings and SupabaseService strip whitespace/quotes and resolve env aliases."""
    # Test 1: Quoted and spaced URL / keys sanitized by Settings
    test_env = {
        "SUPABASE_URL": '  "https://test-project.supabase.co"  ',
        "SUPABASE_ANON_KEY": "  'test-anon-key-123'  ",
        "SUPABASE_SERVICE_ROLE_KEY": '  "test-service-key-456"  ',
        "ENVIRONMENT": "development",
    }
    with patch.dict(os.environ, test_env, clear=False):
        settings = Settings()
        assert settings.SUPABASE_URL == "https://test-project.supabase.co"
        assert settings.SUPABASE_ANON_KEY == "test-anon-key-123"
        assert settings.SUPABASE_SERVICE_ROLE_KEY == "test-service-key-456"

    # Test 2: Fallback to NEXT_PUBLIC_* and SUPABASE_SERVICE_KEY aliases
    alias_env = {
        "SUPABASE_URL": "",
        "SUPABASE_ANON_KEY": "",
        "SUPABASE_SERVICE_ROLE_KEY": "",
        "NEXT_PUBLIC_SUPABASE_URL": '  "https://alias-project.supabase.co"  ',
        "NEXT_PUBLIC_SUPABASE_ANON_KEY": '  "alias-anon-key"  ',
        "SUPABASE_SERVICE_KEY": '  "alias-service-key"  ',
        "ENVIRONMENT": "development",
    }
    with patch.dict(os.environ, alias_env, clear=False):
        settings = Settings()
        assert settings.SUPABASE_URL == "https://alias-project.supabase.co"
        assert settings.SUPABASE_ANON_KEY == "alias-anon-key"
        assert settings.SUPABASE_SERVICE_ROLE_KEY == "alias-service-key"

    # Test 3: Localhost URL in production environment is rejected as offline
    prod_localhost_env = {
        "SUPABASE_URL": "http://localhost:54321",
        "SUPABASE_ANON_KEY": "some-valid-looking-anon-key",
        "SUPABASE_SERVICE_ROLE_KEY": "some-valid-service-key",
        "ENVIRONMENT": "production",
    }
    with patch.dict(os.environ, prod_localhost_env, clear=False):
        settings = Settings()
        svc = SupabaseService.__new__(SupabaseService)
        svc.settings = settings
        svc.client = None
        svc.admin_client = None
        svc._is_live = False
        svc.offline_reason = "Uninitialized"
        svc.initialize()

        assert svc.is_live is False
        assert "localhost" in svc.offline_reason


def test_tracks_query_live_supabase_returns_real_records():
    """Verify GET /api/tracks queries admin.table('tracks') and maps joined artist and album relations."""
    mock_supabase_records = [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "title": "Real Supabase Track 1",
            "artist_id": "art-1",
            "album_id": "alb-1",
            "duration_ms": 210000,
            "track_number": 1,
            "codec": "flac",
            "bitrate": 1411000,
            "file_hash": "hash_real_1",
            "telegram_chat_id": -1009999999,
            "telegram_message_id": 501,
            "uploaded_by": None,
            "is_private": False,
            "file_size": 30000000,
            "ingestion_status": "completed",
            "created_at": "2026-11-18T10:00:00Z",
            "updated_at": "2026-11-18T10:00:00Z",
            "artists": {"id": "art-1", "name": "Real Live Artist"},
            "albums": {
                "id": "alb-1",
                "title": "Real Live Album",
                "artwork_url": "https://example.com/art.jpg",
                "artwork_thumb_url": "https://example.com/art_thumb.jpg",
            },
            "track_artwork": [],
        }
    ]

    mock_admin = MagicMock()
    mock_query = MagicMock()
    mock_query.select.return_value = mock_query
    mock_query.eq.return_value = mock_query
    mock_query.or_.return_value = mock_query
    mock_query.order.return_value = mock_query
    mock_res = MagicMock()
    mock_res.data = mock_supabase_records
    mock_query.order.return_value.execute.return_value = mock_res
    mock_admin.table.return_value = mock_query

    orig_is_live = supabase_service._is_live
    try:
        supabase_service._is_live = True
        with patch.object(supabase_service, "get_admin", return_value=mock_admin):
            res = client.get("/api/tracks?limit=50&offset=0")
            assert res.status_code == 200
            data = res.json()
            assert data["total"] == 1
            assert len(data["items"]) == 1
            track = data["items"][0]
            assert track["id"] == "11111111-1111-1111-1111-111111111111"
            assert track["title"] == "Real Supabase Track 1"
            assert track["artist_name"] == "Real Live Artist"
            assert track["album_title"] == "Real Live Album"
            assert track["artwork_url"] == "https://example.com/art.jpg"
            # Ensure demo tracks ("After Dark", etc.) are NOT in the response
            assert not any(t["title"] == "After Dark" for t in data["items"])
    finally:
        supabase_service._is_live = orig_is_live


def test_format_track_metadata_raw_fallback():
    """Verify that format_track falls back to metadata_raw when relational joins are None."""
    raw_track = {
        "id": "22222222-2222-2222-2222-222222222222",
        "title": "Raw Tag Track",
        "artist_id": None,
        "album_id": None,
        "artists": None,
        "albums": None,
        "track_artwork": [],
        "metadata_raw": {
            "artist": "ID3 Artist Name",
            "album": "ID3 Album Title",
        },
        "duration_ms": 180000,
        "is_private": False,
    }

    formatted = format_track(raw_track)
    assert formatted["artist_name"] == "ID3 Artist Name"
    assert formatted["album_title"] == "ID3 Album Title"


def test_tracks_supabase_query_error_does_not_return_demo_tracks():
    """Verify that when Supabase query fails, HTTP 502 is returned and demo tracks are NEVER served."""
    mock_admin = MagicMock()
    mock_query = MagicMock()
    mock_query.select.return_value = mock_query
    mock_query.eq.return_value = mock_query
    mock_query.or_.return_value = mock_query
    # Simulate database / PostgREST query failure
    mock_query.order.return_value.execute.side_effect = RuntimeError("PostgREST connection failed: 500 Internal Error")
    mock_admin.table.return_value = mock_query

    orig_is_live = supabase_service._is_live
    try:
        supabase_service._is_live = True
        with patch.object(supabase_service, "get_admin", return_value=mock_admin):
            res = client.get("/api/tracks?limit=50&offset=0")
            # Must return 502 Bad Gateway and NEVER 200 with demo tracks
            assert res.status_code == 502
            assert "Failed to query tracks from database" in res.json()["detail"]
    finally:
        supabase_service._is_live = orig_is_live


def test_tracks_production_offline_supabase_returns_503_never_demo_tracks():
    """Verify that when Supabase is offline in production, HTTP 503 is returned and demo tracks are NEVER served."""
    from app.config import get_settings
    settings = get_settings()

    orig_env = settings.ENVIRONMENT
    orig_is_live = supabase_service._is_live
    try:
        settings.ENVIRONMENT = "production"
        supabase_service._is_live = False
        supabase_service.offline_reason = "SUPABASE_URL points to localhost in production"

        res = client.get("/api/tracks?limit=50&offset=0")
        assert res.status_code == 503
        assert "Database service unavailable" in res.json()["detail"]
    finally:
        settings.ENVIRONMENT = orig_env
        supabase_service._is_live = orig_is_live


def test_tracks_dev_offline_serves_mock_store():
    """Verify that in dev/test environment when Supabase is offline, mock store is returned for offline testing."""
    from app.config import get_settings
    settings = get_settings()

    orig_env = settings.ENVIRONMENT
    orig_is_live = supabase_service._is_live
    try:
        settings.ENVIRONMENT = "development"
        supabase_service._is_live = False
        supabase_service.offline_reason = "Offline for unit test"

        res = client.get("/api/tracks?limit=50&offset=0")
        assert res.status_code == 200
        data = res.json()
        assert len(data["items"]) > 0
        assert data["items"][0]["title"] == "After Dark"
    finally:
        settings.ENVIRONMENT = orig_env
        supabase_service._is_live = orig_is_live


def test_supabase_key_classification_and_masking():
    """Verify that key classification correctly identifies key types and safely masks secret material."""
    from app.services.supabase_client import classify_key_format, get_key_source

    # Empty
    assert classify_key_format(None) == {"type": "empty", "prefix": "(empty)", "length": 0}
    assert classify_key_format("") == {"type": "empty", "prefix": "(empty)", "length": 0}

    # Placeholder
    ph = classify_key_format("placeholder-anon-key")
    assert ph["type"] == "placeholder"
    assert ph["prefix"] == "placeholder****"
    assert ph["length"] == len("placeholder-anon-key")

    # Modern publishable key (sb_publishable_...)
    pub = classify_key_format("sb_publishable_example_token_12345")
    assert pub["type"] == "sb_publishable"
    assert pub["prefix"] == "sb_publishable_****"
    assert pub["length"] == len("sb_publishable_example_token_12345")
    assert "example_token" not in pub["prefix"]

    # Modern secret key (sb_secret_...)
    sec = classify_key_format("sb_secret_example_token_67890")
    assert sec["type"] == "sb_secret"
    assert sec["prefix"] == "sb_secret_****"
    assert sec["length"] == len("sb_secret_example_token_67890")
    assert "example_token" not in sec["prefix"]

    # Legacy JWT key
    jwt = classify_key_format("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.signature")
    assert jwt["type"] == "jwt"
    assert jwt["prefix"] == "ey...****"

    # Other unrecognized format
    oth = classify_key_format("custom-secret-key-1234")
    assert oth["type"] == "other"
    assert oth["prefix"] == "cust****"

    # Source detection
    with patch.dict(os.environ, {"SUPABASE_ANON_KEY": "val-1", "NEXT_PUBLIC_SUPABASE_ANON_KEY": "val-2"}):
        assert get_key_source("SUPABASE_ANON_KEY", ["NEXT_PUBLIC_SUPABASE_ANON_KEY"], "val-1") == "SUPABASE_ANON_KEY (direct)"
        assert get_key_source("SUPABASE_ANON_KEY", ["NEXT_PUBLIC_SUPABASE_ANON_KEY"], "val-2") == "NEXT_PUBLIC_SUPABASE_ANON_KEY (alias)"
        assert get_key_source("SUPABASE_ANON_KEY", ["NEXT_PUBLIC_SUPABASE_ANON_KEY"], "placeholder-key") == "default/placeholder"


def test_reproduce_supabase_2_11_0_jwt_regex_failure():
    r"""
    Reproduce the exact root cause of 'SupabaseException: Invalid API key':
    supabase-py <= 2.11.0 strictly required a JWT matching regex:
    ^[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*$
    Modern sb_publishable_* and sb_secret_* keys do not contain dots and fail this check.
    """
    import re

    legacy_pattern = r"^[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*$"

    # Legacy JWT keys pass
    legacy_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.signature"
    assert bool(re.match(legacy_pattern, legacy_jwt)) is True

    # Modern publishable key fails legacy regex
    modern_pub = "sb_publishable_abcdef1234567890"
    assert re.match(legacy_pattern, modern_pub) is None

    # Modern secret key fails legacy regex
    modern_sec = "sb_secret_abcdef1234567890"
    assert re.match(legacy_pattern, modern_sec) is None


def test_supabase_service_initialization_with_modern_keys(caplog):
    """
    Verify that modern Supabase keys (sb_publishable_* and sb_secret_*) initialize
    SupabaseService successfully without 'SupabaseException: Invalid API key',
    sets is_live=True, and logs safe diagnostics without leaking credentials.
    """
    import logging

    test_env = {
        "SUPABASE_URL": "https://test-prod-project.supabase.co",
        "SUPABASE_ANON_KEY": "sb_publishable_live_anon_test_secret_value_123",
        "SUPABASE_SERVICE_ROLE_KEY": "sb_secret_live_service_test_secret_value_456",
        "ENVIRONMENT": "production",
    }

    with patch.dict(os.environ, test_env, clear=False):
        settings = Settings()
        svc = SupabaseService.__new__(SupabaseService)
        svc.settings = settings
        svc.client = None
        svc.admin_client = None
        svc._is_live = False
        svc.offline_reason = "Uninitialized"
        svc.diagnostics = {}

        with caplog.at_level(logging.INFO):
            svc.initialize()

        assert svc.is_live is True
        assert svc.offline_reason == "Connected"
        assert svc.client is not None
        assert svc.admin_client is not None

        # Verify diagnostics structure
        diag = svc.diagnostics
        assert diag["host"] == "test-prod-project.supabase.co"
        assert diag["anon"]["type"] == "sb_publishable"
        assert diag["anon"]["prefix"] == "sb_publishable_****"
        assert diag["anon"]["length"] == len("sb_publishable_live_anon_test_secret_value_123")
        assert diag["service_role"]["type"] == "sb_secret"
        assert diag["service_role"]["prefix"] == "sb_secret_****"
        assert diag["service_role"]["length"] == len("sb_secret_live_service_test_secret_value_456")

        # Verify safe diagnostics in captured logs
        log_text = caplog.text
        assert "Supabase config diagnostics" in log_text
        assert "type=sb_publishable" in log_text
        assert "type=sb_secret" in log_text
        assert "sb_publishable_****" in log_text
        assert "sb_secret_****" in log_text

        # Ensure NO raw secret material was ever logged
        assert "live_anon_test_secret_value_123" not in log_text
        assert "live_service_test_secret_value_456" not in log_text


def test_supabase_compatibility_shim_intercepts_legacy_jwt_check():
    """Verify that _apply_supabase_compatibility_shim patches legacy regex to allow modern keys."""
    from app.services.supabase_client import _apply_supabase_compatibility_shim
    import types
    import re

    # Create mock legacy module where MockLegacySyncClient lives in the module namespace
    mock_mod = types.ModuleType("mock_sync_client_mod")
    mock_mod.re = re

    legacy_code = '''
class MockLegacySyncClient:
    _legacy_jwt_check = True

    def __init__(self, supabase_url: str, supabase_key: str):
        # Check if the key is a valid JWT
        if not re.match(r"^[A-Za-z0-9-_=]+\\.[A-Za-z0-9-_=]+\\.?[A-Za-z0-9-_.+/=]*$", supabase_key):
            raise ValueError("SupabaseException: Invalid API key")
        self.key = supabase_key
'''
    exec(legacy_code, mock_mod.__dict__)
    mock_mod.SyncClient = mock_mod.MockLegacySyncClient

    # Test without shim: modern key raises ValueError
    with pytest.raises(ValueError, match="Invalid API key"):
        mock_mod.SyncClient("https://test.supabase.co", "sb_publishable_test_token_123")

    # Apply shim passing the mock module directly
    _apply_supabase_compatibility_shim(sync_client_mod=mock_mod)

    # With shim applied, modern keys are accepted without error
    inst = mock_mod.SyncClient("https://test.supabase.co", "sb_publishable_test_token_123")
    assert inst.key == "sb_publishable_test_token_123"

    inst2 = mock_mod.SyncClient("https://test.supabase.co", "sb_secret_test_token_456")
    assert inst2.key == "sb_secret_test_token_456"


