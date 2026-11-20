"""Unit and integration tests for Room Sync, In-Room Track Switching, Queue Deduplication, and Library Caching."""

import asyncio
import time
import uuid
from unittest.mock import patch, PropertyMock
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import db
from app.services.supabase_client import SupabaseService
from app.services.track_cache import TrackResponseCache, track_response_cache
from tests.test_backend import generate_valid_tg_init_data

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate_supabase_live():
    """Run tests hermetically against in-memory store."""
    from app.config import get_settings
    settings = get_settings()
    orig_env = settings.ENVIRONMENT
    settings.ENVIRONMENT = "development"
    with patch.object(SupabaseService, "is_live", new_callable=PropertyMock, return_value=False):
        yield
    settings.ENVIRONMENT = orig_env


def get_auth_token(tg_id: int, username: str) -> str:
    """Helper to authenticate a telegram user and retrieve bearer token."""
    res = client.post(
        "/api/auth/telegram",
        json={"init_data": generate_valid_tg_init_data(tg_id=tg_id, username=username)}
    )
    assert res.status_code == 200
    return res.json()["access_token"]


def test_public_config_endpoint():
    """Verify /api/config returns active bot username and environment without leaking secrets."""
    res = client.get("/api/config")
    assert res.status_code == 200
    data = res.json()
    assert data["bot_username"] == "afterhoursfmbot"
    assert "environment" in data
    # Ensure no secrets or tokens are exposed
    assert "token" not in data
    assert "secret" not in data
    assert "api_hash" not in data
    assert "bot_token" not in data


def test_authoritative_change_track():
    """Verify CHANGE_TRACK updates current_track_id, resets position, and schedules playback."""
    host_token = get_auth_token(tg_id=81001, username="host_change_track")
    listener_token = get_auth_token(tg_id=81002, username="listener_change_track")

    host_headers = {"Authorization": f"Bearer {host_token}"}
    listener_headers = {"Authorization": f"Bearer {listener_token}"}

    # Seed mock tracks in db
    track_id = "test_track_123"
    db.tracks[track_id] = {
        "id": track_id,
        "title": "Midnight Drive",
        "artist_name": "Kavinsky",
        "album_title": "OutRun",
        "duration_ms": 210000,
        "is_private": False,
    }

    # Host creates a room
    room_res = client.post(
        "/api/rooms",
        headers=host_headers,
        json={"name": "Night Drive Room", "is_public": True, "max_members": 10}
    )
    assert room_res.status_code == 200
    room_id = room_res.json()["id"]

    # Listener joins room
    join_res = client.post(f"/api/rooms/{room_id}/join", headers=listener_headers)
    assert join_res.status_code == 200

    # Listener attempts CHANGE_TRACK without permission -> 403 Forbidden
    unauth_cmd = client.post(
        f"/api/rooms/{room_id}/command",
        headers=listener_headers,
        json={"action": "CHANGE_TRACK", "payload": {"track_id": track_id, "position_ms": 0}}
    )
    assert unauth_cmd.status_code == 403

    # Host dispatches CHANGE_TRACK with non-existent track -> 404
    missing_cmd = client.post(
        f"/api/rooms/{room_id}/command",
        headers=host_headers,
        json={"action": "CHANGE_TRACK", "payload": {"track_id": "non_existent_id", "position_ms": 0}}
    )
    assert missing_cmd.status_code == 404

    # Host dispatches CHANGE_TRACK with valid track
    change_cmd = client.post(
        f"/api/rooms/{room_id}/command",
        headers=host_headers,
        json={"action": "CHANGE_TRACK", "payload": {"track_id": track_id, "position_ms": 0}}
    )
    assert change_cmd.status_code == 200
    cmd_data = change_cmd.json()
    assert cmd_data["success"] is True
    assert cmd_data["room"]["current_track_id"] == track_id
    assert cmd_data["room"]["is_playing"] is True
    assert cmd_data["room"]["playback_position_ms"] == 0
    assert cmd_data["room"]["scheduled_start_ms"] is not None


def test_next_track_preserves_current_track_when_queue_empty():
    """Verify NEXT_TRACK pauses playback when queue is empty WITHOUT wiping current_track_id."""
    host_token = get_auth_token(tg_id=82001, username="host_empty_queue")
    headers = {"Authorization": f"Bearer {host_token}"}

    track_id = "test_track_keep_id"
    db.tracks[track_id] = {
        "id": track_id,
        "title": "Stay Awake",
        "artist_name": "Afterhours",
        "album_title": "Sessions",
        "duration_ms": 180000,
        "is_private": False,
    }

    # Create room and set current track
    room_res = client.post(
        "/api/rooms",
        headers=headers,
        json={"name": "Preserve Track Room", "is_public": True, "max_members": 10}
    )
    assert room_res.status_code == 200
    room_id = room_res.json()["id"]

    # Set playing track
    client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "CHANGE_TRACK", "payload": {"track_id": track_id, "position_ms": 5000}}
    )

    # Empty the queue explicitly
    db.room_queues[room_id] = []

    # Host executes NEXT_TRACK with empty queue
    next_cmd = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "NEXT_TRACK", "payload": {}}
    )
    assert next_cmd.status_code == 200
    res_data = next_cmd.json()
    assert res_data["success"] is True
    # Crucial check: current_track_id must NOT be wiped/None
    assert res_data["room"]["current_track_id"] == track_id
    # Playback should be paused gracefully
    assert res_data["room"]["is_playing"] is False
    assert res_data["room"]["scheduled_start_ms"] is None


def test_queue_add_deduplication():
    """Verify adding an existing track to queue returns idempotent success with is_duplicate."""
    host_token = get_auth_token(tg_id=83001, username="host_queue_dedup")
    headers = {"Authorization": f"Bearer {host_token}"}

    track_id = "dedup_track_1"
    db.tracks[track_id] = {
        "id": track_id,
        "title": "Deduplicated Song",
        "artist_name": "DJ Idempotent",
        "album_title": "Atomic",
        "duration_ms": 200000,
        "is_private": False,
    }

    room_res = client.post(
        "/api/rooms",
        headers=headers,
        json={"name": "Dedup Queue Room", "is_public": True, "max_members": 10}
    )
    room_id = room_res.json()["id"]
    initial_queue_len = len(db.room_queues.get(room_id, []))

    # First add to queue
    first_add = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "QUEUE_ADD", "payload": {"track_id": track_id}}
    )
    assert first_add.status_code == 200
    assert first_add.json()["success"] is True
    queue_len_1 = len(db.room_queues.get(room_id, []))
    assert queue_len_1 == initial_queue_len + 1

    # Second add to queue with same track -> should be deduplicated
    second_add = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "QUEUE_ADD", "payload": {"track_id": track_id}}
    )
    assert second_add.status_code == 200
    data = second_add.json()
    assert data["success"] is True
    assert data["is_duplicate"] is True
    # Queue length must NOT increase
    queue_len_2 = len(db.room_queues.get(room_id, []))
    assert queue_len_2 == queue_len_1


def test_revision_conflict_409():
    """Verify dispatching command with stale expected_revision raises HTTP 409 Conflict."""
    host_token = get_auth_token(tg_id=84001, username="host_revision_test")
    headers = {"Authorization": f"Bearer {host_token}"}

    room_res = client.post(
        "/api/rooms",
        headers=headers,
        json={"name": "Revision Room", "is_public": True, "max_members": 10}
    )
    room_id = room_res.json()["id"]

    # Dispatch command with intentional outdated revision
    stale_cmd = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "PAUSE", "payload": {"position_ms": 0}, "expected_revision": 9999}
    )
    assert stale_cmd.status_code == 409
    assert "conflict" in stale_cmd.json()["detail"].lower()


def test_track_response_cache_coalescing_and_invalidation():
    """Verify in-flight singleflight request coalescing and cache invalidation."""
    async def run_async_test():
        cache = TrackResponseCache(ttl_seconds=5.0, max_entries=10)
        compute_call_count = 0

        async def expensive_computation():
            nonlocal compute_call_count
            compute_call_count += 1
            await asyncio.sleep(0.05)  # Simulate network / DB latency
            return {"items": [{"id": "t1"}], "total": 1}

        # Simulate 5 concurrent requests arriving simultaneously
        results = await asyncio.gather(
            cache.get_or_compute("key_1", expensive_computation),
            cache.get_or_compute("key_1", expensive_computation),
            cache.get_or_compute("key_1", expensive_computation),
            cache.get_or_compute("key_1", expensive_computation),
            cache.get_or_compute("key_1", expensive_computation),
        )

        # All 5 requests receive identical data, but compute_fn executed exactly ONCE
        assert len(results) == 5
        assert compute_call_count == 1
        assert results[0]["total"] == 1

        # Subsequent request within TTL hits cache directly (compute_call_count still 1)
        cached_res = await cache.get_or_compute("key_1", expensive_computation)
        assert cached_res["total"] == 1
        assert compute_call_count == 1

        # Invalidate cache
        cache.invalidate()

        # Next call must re-compute
        recomputed = await cache.get_or_compute("key_1", expensive_computation)
        assert recomputed["total"] == 1
        assert compute_call_count == 2

    asyncio.run(run_async_test())


def test_artwork_cache_headers():
    """Verify static artwork endpoints return appropriate Cache-Control headers."""
    res_svg = client.get("/api/static/artwork/default.svg")
    assert res_svg.status_code == 200
    assert "public, max-age=" in res_svg.headers.get("Cache-Control", "")
    assert "immutable" in res_svg.headers.get("Cache-Control", "")


def test_solo_host_immediate_playback_and_permissions():
    """Verify solo host can play, pause, seek, and change track immediately with 0ms delay."""
    host_token = get_auth_token(tg_id=85001, username="host_solo_test")
    headers = {"Authorization": f"Bearer {host_token}"}

    track_id = "solo_track_1"
    track_id_2 = "solo_track_2"
    db.tracks[track_id] = {
        "id": track_id,
        "title": "Midnight City",
        "artist_name": "M83",
        "duration_ms": 240000,
        "is_private": False,
    }
    db.tracks[track_id_2] = {
        "id": track_id_2,
        "title": "Wait",
        "artist_name": "M83",
        "duration_ms": 230000,
        "is_private": False,
    }

    # Host creates room alone
    room_res = client.post(
        "/api/rooms",
        headers=headers,
        json={"name": "Solo Listening", "is_public": True, "max_members": 10}
    )
    assert room_res.status_code == 200
    room_id = room_res.json()["id"]

    # 1. Solo host PLAY: sends scheduled_delay_ms=0 for immediate 0ms playback
    play_res = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "PLAY", "payload": {"track_id": track_id, "position_ms": 0, "scheduled_delay_ms": 0}}
    )
    assert play_res.status_code == 200
    play_data = play_res.json()
    assert play_data["success"] is True
    assert play_data["scheduled_start_ms"] is None  # Immediate 0ms playback!
    assert play_data["room"]["is_playing"] is True
    assert play_data["room"]["current_track_id"] == track_id

    # 2. Solo host SEEK
    seek_res = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "SEEK", "payload": {"position_ms": 45000, "scheduled_delay_ms": 0}}
    )
    assert seek_res.status_code == 200
    seek_data = seek_res.json()
    assert seek_data["success"] is True
    assert seek_data["scheduled_start_ms"] is None
    assert seek_data["room"]["playback_position_ms"] == 45000

    # 3. Solo host PAUSE
    pause_res = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "PAUSE", "payload": {"position_ms": 45000}}
    )
    assert pause_res.status_code == 200
    pause_data = pause_res.json()
    assert pause_data["room"]["is_playing"] is False
    assert pause_data["scheduled_start_ms"] is None

    # 4. Solo host CHANGE_TRACK: starts immediately with 0ms delay
    change_res = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "CHANGE_TRACK", "payload": {"track_id": track_id_2, "scheduled_delay_ms": 0}}
    )
    assert change_res.status_code == 200
    change_data = change_res.json()
    assert change_data["scheduled_start_ms"] is None
    assert change_data["room"]["current_track_id"] == track_id_2
    assert change_data["room"]["is_playing"] is True


def test_multi_user_scheduled_sync_delay():
    """Verify that when another listener is in the room, scheduled sync delay is applied."""
    host_token = get_auth_token(tg_id=86001, username="host_sync_test")
    listener_token = get_auth_token(tg_id=86002, username="listener_sync_test")
    host_headers = {"Authorization": f"Bearer {host_token}"}
    listener_headers = {"Authorization": f"Bearer {listener_token}"}

    track_id = "sync_track_1"
    db.tracks[track_id] = {
        "id": track_id,
        "title": "Starboy",
        "artist_name": "The Weeknd",
        "duration_ms": 230000,
        "is_private": False,
    }

    room_res = client.post(
        "/api/rooms",
        headers=host_headers,
        json={"name": "Multi-Device Sync", "is_public": True, "max_members": 10}
    )
    room_id = room_res.json()["id"]

    # Listener joins room
    join_res = client.post(f"/api/rooms/{room_id}/join", headers=listener_headers)
    assert join_res.status_code == 200

    # Multi-device PLAY: scheduled_start_ms should be scheduled in future
    play_res = client.post(
        f"/api/rooms/{room_id}/command",
        headers=host_headers,
        json={"action": "PLAY", "payload": {"track_id": track_id, "position_ms": 0}}
    )
    assert play_res.status_code == 200
    play_data = play_res.json()
    assert play_data["scheduled_start_ms"] is not None
    assert play_data["scheduled_start_ms"] > int(time.time() * 1000)

    # If scheduled_delay_ms is explicitly passed as 0, scheduled_start_ms is None
    zero_delay_res = client.post(
        f"/api/rooms/{room_id}/command",
        headers=host_headers,
        json={"action": "PLAY", "payload": {"track_id": track_id, "position_ms": 0, "scheduled_delay_ms": 0}}
    )
    assert zero_delay_res.status_code == 200
    assert zero_delay_res.json()["scheduled_start_ms"] is None


def test_listener_joining_during_playback():
    """Verify listener joining during active playback receives authoritative track and position without interrupting room."""
    host_token = get_auth_token(tg_id=87001, username="host_ongoing_test")
    listener_token = get_auth_token(tg_id=87002, username="listener_joining_test")
    host_headers = {"Authorization": f"Bearer {host_token}"}
    listener_headers = {"Authorization": f"Bearer {listener_token}"}

    track_id = "ongoing_track_1"
    db.tracks[track_id] = {
        "id": track_id,
        "title": "Nightcall",
        "artist_name": "Kavinsky",
        "duration_ms": 250000,
        "is_private": False,
    }

    room_res = client.post(
        "/api/rooms",
        headers=host_headers,
        json={"name": "Ongoing Room", "is_public": True, "max_members": 10}
    )
    room_id = room_res.json()["id"]

    # Host starts playback alone
    client.post(
        f"/api/rooms/{room_id}/command",
        headers=host_headers,
        json={"action": "PLAY", "payload": {"track_id": track_id, "position_ms": 12000}}
    )

    # Listener joins room
    join_res = client.post(f"/api/rooms/{room_id}/join", headers=listener_headers)
    assert join_res.status_code == 200

    # Listener fetches room snapshot
    room_get = client.get(f"/api/rooms/{room_id}", headers=listener_headers)
    assert room_get.status_code == 200
    room_data = room_get.json()
    assert room_data["is_playing"] is True
    assert room_data["current_track_id"] == track_id
    assert room_data["playback_position_ms"] >= 12000
    assert room_data["current_track"]["title"] == "Nightcall"


def test_library_pagination_and_default_limit_20():
    """Verify GET /api/tracks defaults to limit=20 and supports offset pagination."""
    # Seed 35 mock tracks in db
    for i in range(35):
        tid = f"pagination_track_{i:03d}"
        db.tracks[tid] = {
            "id": tid,
            "title": f"Track Title {i:03d}",
            "artist_name": "Various Artists",
            "album_title": "Compilation",
            "duration_ms": 180000,
            "is_private": False,
            "created_at": f"2026-11-20T10:{i % 60:02d}:00Z",
        }

    # 1. Default request: limit must default to 20
    res_default = client.get("/api/tracks")
    assert res_default.status_code == 200
    data_default = res_default.json()
    assert data_default["limit"] == 20
    assert len(data_default["items"]) == 20
    assert data_default["total"] >= 35

    # 2. Second page: offset=20, limit=20
    res_page2 = client.get("/api/tracks?offset=20&limit=20")
    assert res_page2.status_code == 200
    data_page2 = res_page2.json()
    assert data_page2["offset"] == 20
    assert data_page2["limit"] == 20
    assert len(data_page2["items"]) >= 15

    # Page 1 and Page 2 must not have overlapping track IDs
    ids_page1 = {t["id"] for t in data_default["items"]}
    ids_page2 = {t["id"] for t in data_page2["items"]}
    assert len(ids_page1.intersection(ids_page2)) == 0
