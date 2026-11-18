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
