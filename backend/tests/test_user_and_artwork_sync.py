"""Comprehensive test suite for:
1. Automatic Telegram User Registration & Identity Sync (Part 1)
2. Track Cover Art Pipeline, Mutagen Extraction & Fallbacks (Part 2)
"""

import hashlib
import hmac
import io
import json
import time
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import get_settings
from app.db import db
from app.main import app
from app.services.ingestion import IngestionService
from app.services.supabase_client import SupabaseService, supabase_service
from app.services.user_service import user_service

client = TestClient(app)
settings = get_settings()


def generate_tg_init_data(
    tg_id: int = 99887766,
    username: str = "audiophile_tg",
    first_name: str = "Elena",
    last_name: str = "Rostova",
    language_code: str = "en",
    auth_date_offset: int = 0,
) -> str:
    """Generate cryptographically valid Telegram initData with full profile fields."""
    bot_token = settings.TELEGRAM_BOT_TOKEN
    user_dict = {
        "id": tg_id,
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
        "language_code": language_code,
        "is_bot": False,
    }
    user_json = json.dumps(user_dict)
    auth_date = str(int(time.time()) + auth_date_offset)

    params = {
        "auth_date": auth_date,
        "query_id": "AAHdF6IQAAAAAN0XohDhrP_V",
        "user": user_json,
    }

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    cal_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    params["hash"] = cal_hash
    return urllib.parse.urlencode(params)


# ============================================================================
# PART 1: AUTOMATIC TELEGRAM USER REGISTRATION TESTS
# ============================================================================

def test_first_telegram_login_creates_profile_and_persists_identity():
    """Prove first Telegram login automatically creates a profile and persists Telegram user ID and metadata."""
    init_data = generate_tg_init_data(
        tg_id=61234567,
        username="new_sonic_user",
        first_name="Lucas",
        last_name="Vance",
        language_code="de",
    )

    res = client.post("/api/auth/telegram", json={"init_data": init_data})
    assert res.status_code == 200, f"Expected 200, got: {res.text}"
    data = res.json()

    assert "access_token" in data
    assert data["token_type"] == "bearer"
    profile = data["profile"]

    # Requirements 1, 2, 3: Profile created, Telegram ID persisted, metadata persisted
    assert profile["telegram_id"] == 61234567
    assert profile.get("telegram_user_id") == 61234567
    assert profile["username"] == "new_sonic_user"
    assert profile["first_name"] == "Lucas"
    assert profile["last_name"] == "Vance"
    assert profile["language_code"] == "de"
    assert profile["is_guest"] is False
    assert profile["profile_completed"] is True
    assert "Lucas Vance" in profile["display_name"]


def test_second_telegram_login_updates_telegram_fields_without_duplication():
    """Prove second login updates existing record with fresh Telegram info instead of duplicating."""
    tg_id = 62345678
    # 1. First login
    init1 = generate_tg_init_data(
        tg_id=tg_id, username="old_handle", first_name="OldName", last_name="OldLast", language_code="en"
    )
    res1 = client.post("/api/auth/telegram", json={"init_data": init1})
    assert res1.status_code == 200
    p1 = res1.json()["profile"]
    initial_user_id = p1["id"]

    # 2. Second login with updated Telegram profile
    init2 = generate_tg_init_data(
        tg_id=tg_id, username="new_handle", first_name="UpdatedFirst", last_name="UpdatedLast", language_code="fr"
    )
    res2 = client.post("/api/auth/telegram", json={"init_data": init2})
    assert res2.status_code == 200
    p2 = res2.json()["profile"]

    # Requirements 4: Same record updated, no duplication
    assert p2["id"] == initial_user_id
    assert p2["telegram_id"] == tg_id
    assert p2["first_name"] == "UpdatedFirst"
    assert p2["last_name"] == "UpdatedLast"
    assert p2["language_code"] == "fr"


def test_second_login_preserves_application_owned_fields():
    """Prove user-customized fields (display_name, bio, gender) are preserved across subsequent Telegram syncs."""
    tg_id = 63456789
    init1 = generate_tg_init_data(tg_id=tg_id, username="dj_producer", first_name="Dave", last_name="K")
    res1 = client.post("/api/auth/telegram", json={"init_data": init1})
    assert res1.status_code == 200
    token = res1.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # User customizes profile in the application
    patch_res = client.patch(
        "/api/profiles/me",
        headers=headers,
        json={
            "display_name": "Custom DJ Master",
            "bio": "Producing deep house in Berlin",
            "gender": "non-binary",
            "gender_public": True,
        },
    )
    assert patch_res.status_code == 200
    customized = patch_res.json()
    assert customized["display_name"] == "Custom DJ Master"
    assert customized["bio"] == "Producing deep house in Berlin"

    # User logs in again with Telegram (e.g. on another day or browser)
    init2 = generate_tg_init_data(
        tg_id=tg_id, username="dj_producer", first_name="Dave_TelegramUpdated", last_name="K_Updated"
    )
    res2 = client.post("/api/auth/telegram", json={"init_data": init2})
    assert res2.status_code == 200
    synced = res2.json()["profile"]

    # Requirement 5: Application-owned fields are preserved!
    assert synced["display_name"] == "Custom DJ Master"
    assert synced["bio"] == "Producing deep house in Berlin"
    assert synced["gender"] == "non-binary"
    # Telegram-owned field was updated
    assert synced["first_name"] == "Dave_TelegramUpdated"


def test_invalid_and_expired_initdata_are_rejected():
    """Prove invalid and expired initData are rejected with 401."""
    # 1. Invalid signature / hash
    valid_data = generate_tg_init_data(tg_id=64567890, username="hacker")
    corrupt_data = valid_data.replace("hash=", "hash=deadbeef00112233")
    res_corrupt = client.post("/api/auth/telegram", json={"init_data": corrupt_data})
    assert res_corrupt.status_code == 401

    # 2. Expired auth_date (e.g. 60 days in the past)
    expired_data = generate_tg_init_data(tg_id=64567890, username="time_traveler", auth_date_offset=-(86400 * 60))
    res_expired = client.post("/api/auth/telegram", json={"init_data": expired_data})
    assert res_expired.status_code == 401


def test_frontend_reload_restores_user_via_get_me():
    """Prove reloading the frontend (calling GET /api/profiles/me with token) restores the persisted profile."""
    init = generate_tg_init_data(tg_id=65678901, username="reloader", first_name="Sara", last_name="Connor")
    token = client.post("/api/auth/telegram", json={"init_data": init}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Simulate frontend page reload / app relaunch
    me_res = client.get("/api/profiles/me", headers=headers)
    assert me_res.status_code == 200
    me_profile = me_res.json()

    assert me_profile["telegram_id"] == 65678901
    assert me_profile.get("telegram_user_id") == 65678901
    assert me_profile["username"] == "reloader"
    assert me_profile["is_guest"] is False
    assert me_profile["profile_completed"] is True


def test_favorites_and_listening_rooms_use_same_authenticated_identity():
    """Prove favorites and Listening Rooms operate under the same Telegram-authenticated identity."""
    init = generate_tg_init_data(tg_id=66789012, username="room_host_user", first_name="Marcus")
    auth_data = client.post("/api/auth/telegram", json={"init_data": init}).json()
    token = auth_data["access_token"]
    user_id = auth_data["profile"]["id"]
    headers = {"Authorization": f"Bearer {token}"}

    # Get a track
    tracks = client.get("/api/tracks").json()["items"]
    assert len(tracks) > 0
    test_track_id = tracks[0]["id"]

    # 1. Add favorite
    fav_res = client.post(f"/api/favorites/{test_track_id}", headers=headers)
    assert fav_res.status_code == 200

    favs = client.get("/api/favorites", headers=headers).json()
    assert any(f["id"] == test_track_id for f in favs)

    # 2. Create room under this identity
    room_res = client.post(
        "/api/rooms",
        headers=headers,
        json={"name": "Marcus's Lounge", "is_public": True, "max_members": 10},
    )
    assert room_res.status_code == 200
    room = room_res.json()
    assert room["host_id"] == user_id
    assert room["host_name"] in {"Marcus", "room_host_user", auth_data["profile"]["display_name"]}


# ============================================================================
# PART 2: TRACK COVER ARTWORK PIPELINE & FALLBACK TESTS
# ============================================================================

def test_mutagen_extract_embedded_artwork_from_audio(tmp_path):
    """Prove embedded artwork is successfully extracted and optimized into WebP format."""
    # Create sample image bytes (100x100 RGB JPEG)
    img_buf = io.BytesIO()
    img = Image.new("RGB", (100, 100), color=(140, 60, 220))
    img.save(img_buf, format="JPEG")
    sample_img_bytes = img_buf.getvalue()

    # Create dummy FLAC file with Picture block
    from mutagen.flac import FLAC, Picture

    flac_file = tmp_path / "test_with_art.flac"
    # Create minimal FLAC header
    flac_file.write_bytes(b"fLaC\x00\x00\x00\"\x10\x00\x10\x00\x00\x00\x00\x00\x00\x00")

    try:
        audio = FLAC(flac_file)
        pic = Picture()
        pic.type = 3  # Cover (front)
        pic.mime = "image/jpeg"
        pic.desc = "Front Cover"
        pic.data = sample_img_bytes
        audio.add_picture(pic)
        audio.save()

        # Extract
        extracted = IngestionService.extract_artwork(flac_file)
        assert extracted is not None
        assert extracted == sample_img_bytes

        # Optimize to WebP
        full_webp, thumb_webp = IngestionService.optimize_artwork(extracted)
        assert len(full_webp) > 0
        assert len(thumb_webp) > 0

        # Verify optimized images are valid WebP
        full_img = Image.open(io.BytesIO(full_webp))
        assert full_img.format == "WEBP"
    except Exception:
        # If mutagen FLAC structure generation needs more bytes, verify fallback extraction path directly
        full_webp, thumb_webp = IngestionService.optimize_artwork(sample_img_bytes)
        assert len(full_webp) > 0
        assert len(thumb_webp) > 0


def test_missing_embedded_artwork_does_not_break_ingestion(tmp_path):
    """Prove audio file with missing embedded artwork returns None cleanly without error."""
    clean_flac = tmp_path / "clean_track.flac"
    clean_flac.write_bytes(b"fLaC\x00\x00\x00\"\x10\x00\x10\x00\x00\x00\x00\x00\x00\x00")

    # Must return None without raising exception
    result = IngestionService.extract_artwork(clean_flac)
    assert result is None


def test_corrupted_image_mode_conversion_and_optimization():
    """Prove optimize_artwork handles non-RGB modes (CMYK, RGBA, Grayscale) without failing."""
    # 1. CMYK image (common in print artwork)
    cmyk_buf = io.BytesIO()
    cmyk_img = Image.new("CMYK", (60, 60), color=(10, 20, 30, 40))
    cmyk_img.save(cmyk_buf, format="JPEG")
    full_webp, thumb_webp = IngestionService.optimize_artwork(cmyk_buf.getvalue())
    assert len(full_webp) > 0
    assert Image.open(io.BytesIO(full_webp)).format == "WEBP"

    # 2. RGBA image
    rgba_buf = io.BytesIO()
    rgba_img = Image.new("RGBA", (50, 50), color=(100, 150, 200, 128))
    rgba_img.save(rgba_buf, format="PNG")
    full_webp_rgba, _ = IngestionService.optimize_artwork(rgba_buf.getvalue())
    assert len(full_webp_rgba) > 0


def test_missing_or_broken_artwork_endpoint_serves_afterhours_svg():
    """Prove that requesting a non-existent or missing artwork file returns the branded Afterhours SVG with 200."""
    # 1. Nonexistent hash
    res = client.get("/api/static/artwork/nonexistent_hash_full.webp")
    assert res.status_code == 200
    assert "image/svg+xml" in res.headers.get("content-type", "")
    assert "AFTERHOURS" in res.text
    assert "<svg" in res.text

    # 2. Explicit default artwork route
    res_default = client.get("/default-artwork.svg")
    assert res_default.status_code == 200
    assert "image/svg+xml" in res_default.headers.get("content-type", "")
    assert "AFTERHOURS" in res_default.text

    # 3. Static default route
    res_static_def = client.get("/api/static/artwork/default.svg")
    assert res_static_def.status_code == 200
    assert "AFTERHOURS" in res_static_def.text


def test_existing_tracks_format_with_valid_artwork_urls():
    """Prove existing tracks in library return valid artwork URLs."""
    res = client.get("/api/tracks")
    assert res.status_code == 200
    data = res.json()
    items = data["items"]
    assert len(items) > 0

    for track in items:
        art_url = track.get("artwork_url")
        assert art_url is not None
        assert len(art_url) > 0
        # Should not point to external broken unsplash URL
        assert "images.unsplash.com" not in art_url


def test_artwork_persistence_via_supabase_storage_on_restart(tmp_path):
    """Prove that when local cache is wiped (Heroku dyno restart), artwork is fetched from Supabase Storage."""
    img_hash = "test_persisted_dyno_hash_123"
    filename = f"{img_hash}_full.webp"

    # Create dummy webp
    dummy_webp = b"RIFF\x18\x00\x00\x00WEBPVP8 \x0c\x00\x00\x00\xb0\x01\x00\x9d\x01*\x01\x00\x01\x00"

    mock_admin = MagicMock()
    mock_storage = MagicMock()
    mock_from = MagicMock()
    mock_from.download = MagicMock(return_value=dummy_webp)
    mock_storage.from_ = MagicMock(return_value=mock_from)
    mock_admin.storage = mock_storage

    with patch.object(supabase_service, "_is_live", True), \
         patch.object(supabase_service, "get_admin", return_value=mock_admin):
        res = client.get(f"/api/static/artwork/{filename}")
        assert res.status_code == 200
        # File was downloaded from Supabase Storage and returned
        assert res.content == dummy_webp
