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
    with patch.object(SupabaseService, "is_live", new_callable=PropertyMock, return_value=False):
        yield


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
