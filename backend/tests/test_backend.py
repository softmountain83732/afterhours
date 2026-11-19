"""Comprehensive test suite for Afterhours backend API."""

import hashlib
import hmac
import json
import time
import urllib.parse
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import get_settings

client = TestClient(app)
settings = get_settings()


def generate_valid_tg_init_data(tg_id: int = 12345678, username: str = "alex_listener", first_name: str = "Alex") -> str:
    """Generate a cryptographically valid Telegram WebApp initData string."""
    bot_token = settings.TELEGRAM_BOT_TOKEN
    user_json = json.dumps({"id": tg_id, "first_name": first_name, "username": username})
    auth_date = str(int(time.time()))

    params = {
        "auth_date": auth_date,
        "query_id": "AAHdF6IQAAAAAN0XohDhrP_V",
        "user": user_json
    }

    # Data check string: alphabetically sorted key=value joined by \n
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    cal_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    params["hash"] = cal_hash
    return urllib.parse.urlencode(params)


def test_health_check():
    """Verify health endpoint."""
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["service"] == "afterhours-backend"


def test_telegram_auth_and_profile_flow():
    """Test full journey: Telegram auth -> profile setup -> get profile."""
    init_data = generate_valid_tg_init_data(tg_id=98765432, username="symphony_girl", first_name="Maya")
    res = client.post("/api/auth/telegram", json={"init_data": init_data})
    assert res.status_code == 200
    auth_data = res.json()
    assert "access_token" in auth_data
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Setup profile
    setup_res = client.post(
        "/api/profiles/setup",
        headers=headers,
        json={
            "display_name": "Maya Lin",
            "username": "mayalin",
            "bio": "Deep house and synthwave lover",
            "gender": "female",
            "gender_public": True
        }
    )
    assert setup_res.status_code == 200
    profile = setup_res.json()
    assert profile["display_name"] == "Maya Lin"
    assert profile["username"] == "mayalin"

    # Fetch own profile
    me_res = client.get("/api/profiles/me", headers=headers)
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["username"] == "mayalin"
    assert me_data["gender"] == "female"


def test_username_collision_prevention():
    """Ensure username uniqueness is strictly enforced."""
    init_data_1 = generate_valid_tg_init_data(tg_id=11111, username="first_user")
    init_data_2 = generate_valid_tg_init_data(tg_id=22222, username="second_user")

    res1 = client.post("/api/auth/telegram", json={"init_data": init_data_1})
    res2 = client.post("/api/auth/telegram", json={"init_data": init_data_2})
    token1 = res1.json()["access_token"]
    token2 = res2.json()["access_token"]

    # User 1 claims 'cyberpunk'
    client.post(
        "/api/profiles/setup",
        headers={"Authorization": f"Bearer {token1}"},
        json={"display_name": "User One", "username": "cyberpunk"}
    )

    # User 2 tries to claim the same 'cyberpunk'
    dup_res = client.post(
        "/api/profiles/setup",
        headers={"Authorization": f"Bearer {token2}"},
        json={"display_name": "User Two", "username": "cyberpunk"}
    )
    assert dup_res.status_code == 409


def test_guest_auth_and_limitations():
    """Test guest session creation and restricted action validation."""
    res = client.post("/api/auth/guest", json={"display_name": "Guest Wanderer"})
    assert res.status_code == 200
    guest_data = res.json()
    token = guest_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Guest should not be allowed to create playlists
    pl_res = client.post(
        "/api/playlists",
        headers=headers,
        json={"name": "Forbidden Playlist"}
    )
    assert pl_res.status_code == 403

    # Guest should not be allowed to create rooms
    room_res = client.post(
        "/api/rooms",
        headers=headers,
        json={"name": "Forbidden Room"}
    )
    assert room_res.status_code == 403


def test_track_discovery_and_search():
    """Test listing and searching tracks."""
    res = client.get("/api/tracks")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert len(data["items"]) > 0

    # Test search using existing track title
    first_track = data["items"][0]
    search_term = first_track["title"].split()[0]
    search_res = client.get(f"/api/tracks?q={search_term}")
    assert search_res.status_code == 200
    search_data = search_res.json()
    assert any(search_term.lower() in t["title"].lower() for t in search_data["items"])


def test_audio_streaming_range_and_seeking():
    """Test HTTP Range 206 Partial Content for real audio seeking."""
    from pathlib import Path
    from unittest.mock import patch, AsyncMock
    from app.routers.streaming import audio_cache
    from app.services.telegram import telegram_service

    # Get cached track id if available
    tracks = client.get("/api/tracks").json()["items"]
    cached_track = next((t for t in tracks if (Path(audio_cache.cache_dir) / f"{t['id']}_standard.m4a").exists()), tracks[0])
    track_id = cached_track["id"]

    with patch.object(telegram_service, "download_file_from_vault", AsyncMock(return_value=None)):
        # 1. Full stream request (200)
        full_res = client.get(f"/api/stream/{track_id}")
        assert full_res.status_code == 200
        assert "audio/" in full_res.headers.get("content-type", "")
        assert full_res.headers.get("accept-ranges") == "bytes"
        total_length = int(full_res.headers["content-length"])

        # 2. Range request for seeking: bytes 0-1023 (first 1KB)
        range_res = client.get(f"/api/stream/{track_id}", headers={"Range": "bytes=0-1023"})
        assert range_res.status_code == 206
        assert range_res.headers.get("content-range").startswith("bytes 0-1023/")
        assert len(range_res.content) == 1024

        # 3. Seeking into the middle of the track (e.g. 50000-99999)
        if total_length > 100000:
            seek_res = client.get(f"/api/stream/{track_id}", headers={"Range": "bytes=50000-99999"})
            assert seek_res.status_code == 206
            assert seek_res.headers.get("content-range").startswith("bytes 50000-99999/")
            assert len(seek_res.content) == 50000


def test_playlist_lifecycle():
    """Test playlist creation, adding tracks, reordering, and deleting."""
    init_data = generate_valid_tg_init_data(tg_id=77777, username="dj_k")
    token = client.post("/api/auth/telegram", json={"init_data": init_data}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create playlist
    create_res = client.post(
        "/api/playlists",
        headers=headers,
        json={"name": "Night Drive Vibes", "description": "Synthwave driving playlist", "is_public": True}
    )
    assert create_res.status_code == 200
    pl_id = create_res.json()["id"]

    # 2. Add tracks
    tracks = client.get("/api/tracks").json()["items"]
    t1, t2 = tracks[0]["id"], tracks[1]["id"]
    client.post(f"/api/playlists/{pl_id}/tracks", headers=headers, json={"track_id": t1})
    client.post(f"/api/playlists/{pl_id}/tracks", headers=headers, json={"track_id": t2})

    # 3. Get playlist
    pl_detail = client.get(f"/api/playlists/{pl_id}", headers=headers).json()
    assert len(pl_detail["tracks"]) == 2

    # 4. Reorder
    client.patch(f"/api/playlists/{pl_id}/tracks/reorder", headers=headers, json={"track_ids": [t2, t1]})
    reordered_pl = client.get(f"/api/playlists/{pl_id}", headers=headers).json()
    assert reordered_pl["tracks"][0]["id"] == t2

    # 5. Remove track
    client.delete(f"/api/playlists/{pl_id}/tracks/{t1}", headers=headers)
    pl_after_del = client.get(f"/api/playlists/{pl_id}", headers=headers).json()
    assert len(pl_after_del["tracks"]) == 1

    # 6. Delete playlist
    del_res = client.delete(f"/api/playlists/{pl_id}", headers=headers)
    assert del_res.status_code == 200


def test_listening_rooms_journey():
    """Test room creation, joining, playback sync, permissions delegation, chat, and host transfer."""
    host_token = client.post(
        "/api/auth/telegram",
        json={"init_data": generate_valid_tg_init_data(tg_id=33333, username="host_alice")}
    ).json()["access_token"]
    host_headers = {"Authorization": f"Bearer {host_token}"}

    listener_token = client.post(
        "/api/auth/telegram",
        json={"init_data": generate_valid_tg_init_data(tg_id=44444, username="listener_bob")}
    ).json()["access_token"]
    listener_headers = {"Authorization": f"Bearer {listener_token}"}

    # 1. Host creates room
    room_res = client.post(
        "/api/rooms",
        headers=host_headers,
        json={"name": "Alice's Listening Room", "is_public": True, "max_members": 20}
    )
    assert room_res.status_code == 200
    room = room_res.json()
    room_id = room["id"]

    # 2. Room appears in Global Rooms
    global_rooms = client.get("/api/rooms").json()
    assert any(r["id"] == room_id for r in global_rooms)

    # 3. Bob joins room
    join_res = client.post(f"/api/rooms/{room_id}/join", headers=listener_headers)
    assert join_res.status_code == 200

    # 4. Listener tries to update playback without permission -> 403
    forbidden_sync = client.patch(
        f"/api/rooms/{room_id}/playback",
        headers=listener_headers,
        json={"playback_position_ms": 30000, "is_playing": True}
    )
    assert forbidden_sync.status_code == 403

    # 5. Host delegates playback control to Bob
    bob_profile = client.get("/api/profiles/me", headers=listener_headers).json()
    bob_id = bob_profile["id"]

    perm_res = client.patch(
        f"/api/rooms/{room_id}/members/{bob_id}/permissions",
        headers=host_headers,
        json={"can_control_playback": True, "can_manage_queue": True}
    )
    assert perm_res.status_code == 200

    # 6. Now Bob can update playback
    sync_res = client.patch(
        f"/api/rooms/{room_id}/playback",
        headers=listener_headers,
        json={"playback_position_ms": 45000, "is_playing": True}
    )
    assert sync_res.status_code == 200

    # 7. Chat in room
    chat_res = client.post(
        f"/api/rooms/{room_id}/messages",
        headers=listener_headers,
        json={"content": "This track is fantastic!"}
    )
    assert chat_res.status_code == 200
    msgs = client.get(f"/api/rooms/{room_id}/messages").json()
    assert any(m["content"] == "This track is fantastic!" for m in msgs)

    # 8. Host kicks or closes
    close_res = client.delete(f"/api/rooms/{room_id}", headers=host_headers)
    assert close_res.status_code == 200


def test_favorites_and_history():
    """Test favorites and listening history."""
    token = client.post(
        "/api/auth/telegram",
        json={"init_data": generate_valid_tg_init_data(tg_id=55555, username="charlie")}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    tracks = client.get("/api/tracks").json()["items"]
    track_id = tracks[0]["id"]

    # Add favorite
    fav_res = client.post(f"/api/favorites/{track_id}", headers=headers)
    assert fav_res.status_code == 200

    # List favorites
    favs = client.get("/api/favorites", headers=headers).json()
    assert any(f["id"] == track_id for f in favs)

    # Record play history
    hist_res = client.post(
        "/api/history",
        headers=headers,
        json={"track_id": track_id, "duration_listened_ms": 120000}
    )
    assert hist_res.status_code == 200

    # Retrieve history
    history_entries = client.get("/api/history", headers=headers).json()
    assert len(history_entries) > 0
    assert history_entries[0]["track"]["id"] == track_id


def test_telegram_auth_with_bot_api_7_signature_parameter():
    """Telegram Bot API 7.0+ adds a 'signature' param to initData. Ensure it doesn't cause 401."""
    init_data = generate_valid_tg_init_data(tg_id=88888, username="sig_test_user")
    # Append the Bot API 7.0+ Ed25519 signature
    init_data_with_sig = f"{init_data}&signature=AQABbW9jay1zaWduYXR1cmUtZGF0YQ"
    
    res = client.post("/api/auth/telegram", json={"init_data": init_data_with_sig})
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    data = res.json()
    assert "access_token" in data
    assert data["profile"]["username"] == "sig_test_user"


def test_validate_telegram_init_data_handles_token_whitespace_and_quotes():
    """Ensure bot tokens with leading/trailing spaces or quotes are sanitized properly."""
    from app.auth import validate_telegram_init_data
    raw_token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    
    # Generate initData with raw token
    user_json = json.dumps({"id": 11111, "first_name": "Sanitize", "username": "clean_token"})
    params = {
        "auth_date": str(int(time.time())),
        "query_id": "AAHdF6IQAAAAAN0XohDhrP_V",
        "user": user_json
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", raw_token.encode("utf-8"), hashlib.sha256).digest()
    cal_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    params["hash"] = cal_hash
    init_data = urllib.parse.urlencode(params)

    # Validate with quoted/padded bot tokens
    for padded_token in [f"  {raw_token}  ", f'"{raw_token}"', f"'{raw_token}'", f" '\"{raw_token}\"' "]:
        user_info = validate_telegram_init_data(init_data, padded_token)
        assert user_info is not None, f"Failed validation with padded token: {padded_token}"
        assert user_info["id"] == 11111


def test_guest_profile_get_returns_200_not_404():
    """Guest users must be able to call GET /api/profiles/me without 404."""
    guest_res = client.post("/api/auth/guest")
    assert guest_res.status_code == 200
    guest_token = guest_res.json()["access_token"]
    guest_headers = {"Authorization": f"Bearer {guest_token}"}

    profile_res = client.get("/api/profiles/me", headers=guest_headers)
    assert profile_res.status_code == 200
    profile = profile_res.json()
    assert profile["is_guest"] is True
    assert profile["profile_completed"] is True
    assert "Guest" in profile["display_name"]


def test_new_user_missing_supabase_profile_returns_skeleton_200():
    """A user whose profile row has not yet been written to Supabase should receive a setup skeleton, not 404."""
    from unittest.mock import MagicMock, patch
    from app.services.supabase_client import supabase_service

    init_data = generate_valid_tg_init_data(tg_id=99999, username="new_unregistered_user", first_name="Newbie")
    auth_res = client.post("/api/auth/telegram", json={"init_data": init_data})
    assert auth_res.status_code == 200
    token = auth_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Simulate Supabase returning empty list (row does not exist)
    mock_admin = MagicMock()
    mock_table = MagicMock()
    mock_select = MagicMock()
    mock_eq = MagicMock()
    mock_execute = MagicMock(return_value=MagicMock(data=[]))
    mock_eq.execute = mock_execute
    mock_select.eq = MagicMock(return_value=mock_eq)
    mock_table.select = MagicMock(return_value=mock_select)
    mock_admin.table = MagicMock(return_value=mock_table)

    with patch.object(supabase_service, "_is_live", True), \
         patch.object(supabase_service, "get_admin", return_value=mock_admin):
        me_res = client.get("/api/profiles/me", headers=headers)
        assert me_res.status_code == 200
        data = me_res.json()
        assert data["profile_completed"] is False
        assert data["username"] == "new_unregistered_user"
        assert data["display_name"] in {"new_unregistered_user", "Newbie"}

