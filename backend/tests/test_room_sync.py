"""Unit and integration tests for Spotify Jam-style Listening Room synchronization and permissions."""

import time
from datetime import datetime
from unittest.mock import patch, PropertyMock
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import db
from app.services.supabase_client import SupabaseService
try:
    from tests.test_backend import generate_valid_tg_init_data
except ImportError:
    from backend.tests.test_backend import generate_valid_tg_init_data

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate_supabase_live():
    """Ensure room sync unit tests run hermetically against in-memory store."""
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


def test_clock_sync_endpoint():
    """Verify NTP-style clock sync endpoint returns client echo and accurate server timestamp."""
    client_echo = 1731855000123
    t_before = int(time.time() * 1000)
    res = client.get(f"/api/rooms/sync/clock?client_echo={client_echo}")
    t_after = int(time.time() * 1000)

    assert res.status_code == 200
    data = res.json()
    assert data["client_echo"] == client_echo
    assert "server_time_ms" in data
    assert "server_iso" in data
    assert t_before - 100 <= data["server_time_ms"] <= t_after + 100


def test_scheduled_playback_lead_time_and_bounds():
    """Verify default 800ms scheduled lead time and 500-1500ms clamping."""
    host_token = get_auth_token(tg_id=90001, username="host_sync_1")
    headers = {"Authorization": f"Bearer {host_token}"}

    room_res = client.post(
        "/api/rooms",
        headers=headers,
        json={"name": "Scheduled Playback Room", "is_public": True, "max_members": 10}
    )
    room_id = room_res.json()["id"]

    # 1. Default lead time (800ms)
    play_res = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "PLAY", "payload": {"position_ms": 10000}}
    )
    assert play_res.status_code == 200
    data = play_res.json()
    assert data["scheduled_start_ms"] is not None
    server_time_ms = int(datetime.fromisoformat(data["server_timestamp"]).timestamp() * 1000)
    diff = data["scheduled_start_ms"] - server_time_ms
    assert 750 <= diff <= 850

    # 2. Clamped minimum lead time (clamped to 500ms when request says 100ms)
    play_min = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "PLAY", "payload": {"position_ms": 12000, "scheduled_delay_ms": 100}}
    )
    assert play_min.status_code == 200
    min_data = play_min.json()
    server_time_min_ms = int(datetime.fromisoformat(min_data["server_timestamp"]).timestamp() * 1000)
    diff_min = min_data["scheduled_start_ms"] - server_time_min_ms
    assert 450 <= diff_min <= 550

    # 3. Clamped maximum lead time (clamped to 1500ms when request says 5000ms)
    play_max = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "PLAY", "payload": {"position_ms": 15000, "scheduled_delay_ms": 5000}}
    )
    assert play_max.status_code == 200
    max_data = play_max.json()
    server_time_max_ms = int(datetime.fromisoformat(max_data["server_timestamp"]).timestamp() * 1000)
    diff_max = max_data["scheduled_start_ms"] - server_time_max_ms
    assert 1450 <= diff_max <= 1550


def test_monotonic_revision_conflict_rejection():
    """Verify that commands with mismatched expected_revision are rejected with HTTP 409 Conflict."""
    host_token = get_auth_token(tg_id=90002, username="host_sync_2")
    headers = {"Authorization": f"Bearer {host_token}"}

    room_res = client.post(
        "/api/rooms",
        headers=headers,
        json={"name": "Revision Room", "is_public": True}
    )
    room = room_res.json()
    room_id = room["id"]
    current_rev = room.get("revision", 1)

    # Command with matching expected revision succeeds
    cmd1 = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "PAUSE", "expected_revision": current_rev, "payload": {"position_ms": 5000}}
    )
    assert cmd1.status_code == 200
    new_rev = cmd1.json()["revision"]
    assert new_rev == current_rev + 1

    # Stale command with old revision must fail with 409
    cmd_stale = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "PLAY", "expected_revision": current_rev, "payload": {"position_ms": 5000}}
    )
    assert cmd_stale.status_code == 409
    assert "revision conflict" in cmd_stale.json()["detail"].lower()


def test_permission_enforcement_matrix():
    """Verify Host + Custom Guest permissions and role delegation."""
    host_token = get_auth_token(tg_id=90003, username="host_perm")
    listener_token = get_auth_token(tg_id=90004, username="listener_perm")
    host_headers = {"Authorization": f"Bearer {host_token}"}
    listener_headers = {"Authorization": f"Bearer {listener_token}"}

    room = client.post(
        "/api/rooms",
        headers=host_headers,
        json={"name": "Perm Room", "is_public": True}
    ).json()
    room_id = room["id"]

    # Listener joins room
    client.post(f"/api/rooms/{room_id}/join", headers=listener_headers)
    listener_profile = client.get("/api/auth/me", headers=listener_headers).json()
    listener_id = listener_profile["id"]

    # 1. Listener cannot execute PLAY without permission -> 403
    p_fail = client.post(
        f"/api/rooms/{room_id}/command",
        headers=listener_headers,
        json={"action": "PLAY", "payload": {"position_ms": 0}}
    )
    assert p_fail.status_code == 403

    # 2. Host grants can_control_playback
    grant_res = client.patch(
        f"/api/rooms/{room_id}/members/{listener_id}/permissions",
        headers=host_headers,
        json={"can_control_playback": True}
    )
    assert grant_res.status_code == 200

    # 3. Listener can now execute PLAY
    p_ok = client.post(
        f"/api/rooms/{room_id}/command",
        headers=listener_headers,
        json={"action": "PLAY", "payload": {"position_ms": 2000}}
    )
    assert p_ok.status_code == 200

    # 4. Listener cannot manage queue yet -> 403
    q_fail = client.post(
        f"/api/rooms/{room_id}/command",
        headers=listener_headers,
        json={"action": "QUEUE_ADD", "payload": {"track_id": "dummy_track"}}
    )
    assert q_fail.status_code == 403

    # 5. Host assigns DJ role
    role_res = client.patch(
        f"/api/rooms/{room_id}/members/{listener_id}/permissions",
        headers=host_headers,
        json={"role": "dj"}
    )
    assert role_res.status_code == 200

    # 6. DJ can manage queue
    q_ok = client.post(
        f"/api/rooms/{room_id}/command",
        headers=listener_headers,
        json={"action": "QUEUE_ADD", "payload": {"track_id": "dummy_track_1"}}
    )
    assert q_ok.status_code == 200


def test_normalized_queue_and_dual_write_transaction():
    """Verify queue items create normalized entries and dual-write to legacy rooms.queue."""
    host_token = get_auth_token(tg_id=90005, username="host_queue")
    headers = {"Authorization": f"Bearer {host_token}"}

    room = client.post(
        "/api/rooms",
        headers=headers,
        json={"name": "Queue Room", "is_public": True}
    ).json()
    room_id = room["id"]

    # Add 2 tracks via command dispatcher
    client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "QUEUE_ADD", "payload": {"track_id": "track_alpha"}}
    )
    client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "QUEUE_ADD", "payload": {"track_id": "track_beta"}}
    )

    # Verify normalized queue endpoint
    norm_queue = client.get(f"/api/rooms/{room_id}/queue").json()
    assert len(norm_queue) >= 2
    track_ids = [item["track_id"] for item in norm_queue]
    assert "track_alpha" in track_ids
    assert "track_beta" in track_ids

    # Verify legacy rooms.queue dual-write
    room_detail = client.get(f"/api/rooms/{room_id}", headers=headers).json()
    assert "track_alpha" in room_detail["queue"]
    assert "track_beta" in room_detail["queue"]


def test_collaborative_queue_voting_and_dynamic_resort():
    """Verify room members can vote on queue items and the queue re-sorts dynamically."""
    host_token = get_auth_token(tg_id=90006, username="host_vote")
    listener_token = get_auth_token(tg_id=90007, username="listener_vote")
    host_headers = {"Authorization": f"Bearer {host_token}"}
    listener_headers = {"Authorization": f"Bearer {listener_token}"}

    room = client.post(
        "/api/rooms",
        headers=host_headers,
        json={"name": "Voting Room", "is_public": True}
    ).json()
    room_id = room["id"]

    client.post(f"/api/rooms/{room_id}/join", headers=listener_headers)

    # Host adds 2 tracks
    client.post(
        f"/api/rooms/{room_id}/command",
        headers=host_headers,
        json={"action": "QUEUE_ADD", "payload": {"track_id": "low_vote_track"}}
    )
    client.post(
        f"/api/rooms/{room_id}/command",
        headers=host_headers,
        json={"action": "QUEUE_ADD", "payload": {"track_id": "high_vote_track"}}
    )

    # Get queue items to find item_id
    queue = client.get(f"/api/rooms/{room_id}/queue").json()
    high_item = next(i for i in queue if i["track_id"] == "high_vote_track")

    # Listener votes for high_vote_track (+1)
    vote_res = client.post(
        f"/api/rooms/{room_id}/command",
        headers=listener_headers,
        json={"action": "QUEUE_VOTE", "payload": {"queue_item_id": high_item["id"], "vote": 1}}
    )
    assert vote_res.status_code == 200

    # Check updated normalized queue order - high_vote_track should now be ahead
    updated_queue = client.get(f"/api/rooms/{room_id}/queue").json()
    first_item = updated_queue[0]
    assert first_item["track_id"] == "high_vote_track"
    assert first_item["vote_count"] == 1


def test_next_track_advancement():
    """Verify NEXT_TRACK marks current item played and advances to top queued track."""
    host_token = get_auth_token(tg_id=90008, username="host_advance")
    headers = {"Authorization": f"Bearer {host_token}"}

    room = client.post(
        "/api/rooms",
        headers=headers,
        json={"name": "Advance Room", "is_public": True}
    ).json()
    room_id = room["id"]

    client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "QUEUE_ADD", "payload": {"track_id": "future_track_1"}}
    )

    # First advance shifts out initial track in queue
    advance_res1 = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "NEXT_TRACK"}
    )
    assert advance_res1.status_code == 200

    # Second advance shifts to future_track_1
    advance_res2 = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "NEXT_TRACK"}
    )
    assert advance_res2.status_code == 200
    room_state = advance_res2.json()["room"]
    assert room_state["current_track_id"] == "future_track_1"
    assert room_state["is_playing"] is True
    assert room_state["scheduled_start_ms"] is not None


def test_moderator_privilege_escalation_prevention():
    """Verify moderators cannot escalate privileges, modify host, kick other moderators, or assign host role."""
    host_token = get_auth_token(tg_id=90010, username="host_esc")
    mod_token = get_auth_token(tg_id=90011, username="mod_esc")
    mod2_token = get_auth_token(tg_id=90012, username="mod2_esc")
    listener_token = get_auth_token(tg_id=90013, username="listener_esc")

    host_headers = {"Authorization": f"Bearer {host_token}"}
    mod_headers = {"Authorization": f"Bearer {mod_token}"}
    mod2_headers = {"Authorization": f"Bearer {mod2_token}"}
    listener_headers = {"Authorization": f"Bearer {listener_token}"}

    room = client.post("/api/rooms", headers=host_headers, json={"name": "Escalation Room", "is_public": True}).json()
    room_id = room["id"]
    host_id = room["host_id"]

    # Join members
    client.post(f"/api/rooms/{room_id}/join", headers=mod_headers)
    client.post(f"/api/rooms/{room_id}/join", headers=mod2_headers)
    client.post(f"/api/rooms/{room_id}/join", headers=listener_headers)

    mod_id = client.get("/api/auth/me", headers=mod_headers).json()["id"]
    mod2_id = client.get("/api/auth/me", headers=mod2_headers).json()["id"]
    listener_id = client.get("/api/auth/me", headers=listener_headers).json()["id"]

    # Host promotes mod to moderator role
    p1 = client.patch(
        f"/api/rooms/{room_id}/members/{mod_id}/permissions",
        headers=host_headers,
        json={"role": "moderator", "can_moderate": True}
    )
    assert p1.status_code == 200

    # Host promotes mod2 to moderator role
    p2 = client.patch(
        f"/api/rooms/{room_id}/members/{mod2_id}/permissions",
        headers=host_headers,
        json={"role": "moderator", "can_moderate": True}
    )
    assert p2.status_code == 200

    # 1. Moderator cannot modify actual host's permissions -> 400
    res_mod_host = client.patch(
        f"/api/rooms/{room_id}/members/{host_id}/permissions",
        headers=mod_headers,
        json={"can_control_playback": False}
    )
    assert res_mod_host.status_code == 400
    assert "Cannot modify host permissions" in res_mod_host.json()["detail"]

    # 2. Moderator cannot promote anyone or self to host -> 403
    res_promote_self = client.patch(
        f"/api/rooms/{room_id}/members/{mod_id}/permissions",
        headers=mod_headers,
        json={"role": "host"}
    )
    assert res_promote_self.status_code == 403
    assert "Host role can only be assigned" in res_promote_self.json()["detail"]

    res_promote_other = client.patch(
        f"/api/rooms/{room_id}/members/{listener_id}/permissions",
        headers=mod_headers,
        json={"role": "host"}
    )
    assert res_promote_other.status_code == 403

    # 3. Moderator cannot grant or modify moderator privileges -> 403
    res_grant_mod = client.patch(
        f"/api/rooms/{room_id}/members/{listener_id}/permissions",
        headers=mod_headers,
        json={"can_moderate": True}
    )
    assert res_grant_mod.status_code == 403
    assert "Only host can grant or modify moderator privileges" in res_grant_mod.json()["detail"]

    # 4. Moderator cannot modify another moderator -> 403
    res_edit_mod = client.patch(
        f"/api/rooms/{room_id}/members/{mod2_id}/permissions",
        headers=mod_headers,
        json={"can_control_playback": False}
    )
    assert res_edit_mod.status_code == 403
    assert "Moderators cannot modify other moderators" in res_edit_mod.json()["detail"]

    # 5. Moderator cannot kick another moderator -> 403
    res_kick_mod = client.delete(
        f"/api/rooms/{room_id}/members/{mod2_id}",
        headers=mod_headers
    )
    assert res_kick_mod.status_code == 403
    assert "Moderators cannot kick other moderators" in res_kick_mod.json()["detail"]

    # 6. Moderator cannot kick the room host -> 400
    res_kick_host = client.delete(
        f"/api/rooms/{room_id}/members/{host_id}",
        headers=mod_headers
    )
    assert res_kick_host.status_code == 400
    assert "Cannot kick the room host" in res_kick_host.json()["detail"]

    # 7. Modifying non-member permissions returns 404
    res_non_member = client.patch(
        f"/api/rooms/{room_id}/members/non_existent_user_id/permissions",
        headers=host_headers,
        json={"can_control_playback": True}
    )
    assert res_non_member.status_code == 404


def test_cross_room_queue_voting_and_manipulation_rejected():
    """Verify queue voting and removal are strictly scoped to room_id, rejecting cross-room tampering."""
    host_token_a = get_auth_token(tg_id=90020, username="host_room_a")
    host_token_b = get_auth_token(tg_id=90021, username="host_room_b")
    listener_token = get_auth_token(tg_id=90022, username="listener_cross")

    headers_a = {"Authorization": f"Bearer {host_token_a}"}
    headers_b = {"Authorization": f"Bearer {host_token_b}"}
    listener_headers = {"Authorization": f"Bearer {listener_token}"}

    room_a = client.post("/api/rooms", headers=headers_a, json={"name": "Room A", "is_public": True}).json()
    room_b = client.post("/api/rooms", headers=headers_b, json={"name": "Room B", "is_public": True}).json()
    room_a_id = room_a["id"]
    room_b_id = room_b["id"]

    # Add item to Room A
    client.post(
        f"/api/rooms/{room_a_id}/command",
        headers=headers_a,
        json={"action": "QUEUE_ADD", "payload": {"track_id": "track_in_room_a"}}
    )
    queue_a = client.get(f"/api/rooms/{room_a_id}/queue").json()
    item_a = queue_a[0]
    item_a_id = item_a["id"]

    # Add item to Room B
    client.post(
        f"/api/rooms/{room_b_id}/command",
        headers=headers_b,
        json={"action": "QUEUE_ADD", "payload": {"track_id": "track_in_room_b"}}
    )

    # Listener joins Room B
    client.post(f"/api/rooms/{room_b_id}/join", headers=listener_headers)

    # 1. Attempt to vote in Room B using Room A's queue_item_id -> rejected with 403
    vote_cross = client.post(
        f"/api/rooms/{room_b_id}/command",
        headers=listener_headers,
        json={"action": "QUEUE_VOTE", "payload": {"queue_item_id": item_a_id, "vote": 1}}
    )
    assert vote_cross.status_code == 403
    assert "does not belong to this room" in vote_cross.json()["detail"].lower()

    # 2. Attempt to remove item from Room B using Room A's queue_item_id -> rejected with 403
    remove_cross = client.post(
        f"/api/rooms/{room_b_id}/command",
        headers=headers_b,
        json={"action": "QUEUE_REMOVE", "payload": {"queue_item_id": item_a_id}}
    )
    assert remove_cross.status_code == 403
    assert "does not belong to this room" in remove_cross.json()["detail"].lower()

    # 3. Nonexistent item ID returns 404
    vote_nonexistent = client.post(
        f"/api/rooms/{room_b_id}/command",
        headers=listener_headers,
        json={"action": "QUEUE_VOTE", "payload": {"queue_item_id": "non_existent_item", "vote": 1}}
    )
    assert vote_nonexistent.status_code == 404


def test_legacy_queue_endpoint_preserves_normalized_records_and_votes():
    """Verify legacy POST /api/rooms/{room_id}/queue reconciles without wiping normalized records or votes."""
    host_token = get_auth_token(tg_id=90030, username="host_legacy")
    listener_token = get_auth_token(tg_id=90031, username="listener_legacy")

    host_headers = {"Authorization": f"Bearer {host_token}"}
    listener_headers = {"Authorization": f"Bearer {listener_token}"}

    room = client.post("/api/rooms", headers=host_headers, json={"name": "Legacy Sync Room", "is_public": True}).json()
    room_id = room["id"]

    # 1. Unauthorized listener cannot call legacy queue endpoint -> 403
    client.post(f"/api/rooms/{room_id}/join", headers=listener_headers)
    unauth_res = client.post(
        f"/api/rooms/{room_id}/queue",
        headers=listener_headers,
        json={"queue": ["track_1"]}
    )
    assert unauth_res.status_code == 403
    assert "Queue management permission required" in unauth_res.json()["detail"]

    # 2. Host adds two tracks via sync commands
    client.post(
        f"/api/rooms/{room_id}/command",
        headers=host_headers,
        json={"action": "QUEUE_ADD", "payload": {"track_id": "track_alpha"}}
    )
    client.post(
        f"/api/rooms/{room_id}/command",
        headers=host_headers,
        json={"action": "QUEUE_ADD", "payload": {"track_id": "track_beta"}}
    )

    q_initial = client.get(f"/api/rooms/{room_id}/queue").json()
    alpha_item = next(i for i in q_initial if i["track_id"] == "track_alpha")
    beta_item = next(i for i in q_initial if i["track_id"] == "track_beta")
    alpha_id = alpha_item["id"]
    beta_id = beta_item["id"]

    # Listener votes on track_alpha (+1)
    client.post(
        f"/api/rooms/{room_id}/command",
        headers=listener_headers,
        json={"action": "QUEUE_VOTE", "payload": {"queue_item_id": alpha_id, "vote": 1}}
    )

    q_voted = client.get(f"/api/rooms/{room_id}/queue").json()
    voted_alpha = next(i for i in q_voted if i["id"] == alpha_id)
    assert voted_alpha["vote_count"] == 1

    # 3. Host calls legacy endpoint to re-order and add a third track [track_beta, track_alpha, track_gamma]
    legacy_res = client.post(
        f"/api/rooms/{room_id}/queue",
        headers=host_headers,
        json={"queue": ["track_beta", "track_alpha", "track_gamma"]}
    )
    assert legacy_res.status_code == 200

    # 4. Check normalized queue: alpha and beta IDs must be preserved, alpha's vote count must be 1, gamma is added
    q_reconciled = client.get(f"/api/rooms/{room_id}/queue").json()
    rec_alpha = next(i for i in q_reconciled if i["track_id"] == "track_alpha")
    rec_beta = next(i for i in q_reconciled if i["track_id"] == "track_beta")
    rec_gamma = next(i for i in q_reconciled if i["track_id"] == "track_gamma")

    assert rec_alpha["id"] == alpha_id, "Original queue item ID for track_alpha was wiped!"
    assert rec_alpha["vote_count"] == 1, "Vote count for track_alpha was wiped by legacy update!"
    assert rec_beta["id"] == beta_id, "Original queue item ID for track_beta was wiped!"
    assert rec_gamma["vote_count"] == 0


def test_rpc_fallback_error_classification_unit():
    """Verify classify_rpc_error correctly separates missing RPC from business/concurrency/DB errors."""
    from app.services.room_sync import classify_rpc_error

    # 1. Genuine missing RPC in PostgREST -> allow fallback
    err_missing = Exception({"code": "PGRST202", "message": "Could not find the function add_room_queue_item_atomic in the schema cache"})
    can_fallback, exc = classify_rpc_error(err_missing)
    assert can_fallback is True
    assert exc is None

    err_missing_42883 = Exception({"code": "42883", "message": "function add_room_queue_item_atomic does not exist"})
    can_fallback, exc = classify_rpc_error(err_missing_42883)
    assert can_fallback is True
    assert exc is None

    # 2. Concurrency Conflict P0004 -> HTTP 409, no fallback
    err_p0004 = Exception({"code": "P0004", "message": "Revision conflict: expected 1, current 2"})
    can_fallback, exc = classify_rpc_error(err_p0004)
    assert can_fallback is False
    assert exc is not None
    assert exc.status_code == 409

    # 3. Inactive Room P0003 -> HTTP 400, no fallback
    err_p0003 = Exception({"code": "P0003", "message": "Room is inactive"})
    can_fallback, exc = classify_rpc_error(err_p0003)
    assert can_fallback is False
    assert exc is not None
    assert exc.status_code == 400

    # 4. Cross-Room or Permission 42501 -> HTTP 403, no fallback
    err_42501 = Exception({"code": "42501", "message": "Queue item does not belong to this room"})
    can_fallback, exc = classify_rpc_error(err_42501)
    assert can_fallback is False
    assert exc is not None
    assert exc.status_code == 403

    # 5. Already Played Item P0006 -> HTTP 400, no fallback
    err_p0006 = Exception({"code": "P0006", "message": "Cannot vote on played queue item"})
    can_fallback, exc = classify_rpc_error(err_p0006)
    assert can_fallback is False
    assert exc is not None
    assert exc.status_code == 400

    # 6. Unknown / DB crash / Network failure -> propagate, no fallback
    err_unknown = Exception("Connection refused to database host")
    can_fallback, exc = classify_rpc_error(err_unknown)
    assert can_fallback is False
    assert exc is None


def test_voting_on_played_item_rejected():
    """Verify voting on an item that has already played returns HTTP 400."""
    host_token = get_auth_token(tg_id=90040, username="host_vote_played")
    listener_token = get_auth_token(tg_id=90041, username="listener_vote_played")
    host_headers = {"Authorization": f"Bearer {host_token}"}
    listener_headers = {"Authorization": f"Bearer {listener_token}"}

    room = client.post("/api/rooms", headers=host_headers, json={"name": "Played Vote Room", "is_public": True}).json()
    room_id = room["id"]

    client.post(f"/api/rooms/{room_id}/join", headers=listener_headers)

    # Host adds a track
    client.post(
        f"/api/rooms/{room_id}/command",
        headers=host_headers,
        json={"action": "QUEUE_ADD", "payload": {"track_id": "track_to_play"}}
    )
    queue = client.get(f"/api/rooms/{room_id}/queue").json()
    item = next(i for i in queue if i["track_id"] == "track_to_play")
    item_id = item["id"]

    # Advance twice to play the track
    client.post(f"/api/rooms/{room_id}/command", headers=host_headers, json={"action": "NEXT_TRACK"})
    client.post(f"/api/rooms/{room_id}/command", headers=host_headers, json={"action": "NEXT_TRACK"})

    # Now attempt to vote on item_id -> must be rejected with 400
    res_vote = client.post(
        f"/api/rooms/{room_id}/command",
        headers=listener_headers,
        json={"action": "QUEUE_VOTE", "payload": {"queue_item_id": item_id, "vote": 1}}
    )
    assert res_vote.status_code == 400
    assert "Cannot vote on a played queue item" in res_vote.json()["detail"]


def test_next_track_atomic_state_and_concurrency_conflict():
    """Verify NEXT_TRACK increments revision monotonically and rejects conflicting expected_revision."""
    host_token = get_auth_token(tg_id=90045, username="host_next_atomic")
    headers = {"Authorization": f"Bearer {host_token}"}

    room = client.post("/api/rooms", headers=headers, json={"name": "Next Atomic Room", "is_public": True}).json()
    room_id = room["id"]
    rev_0 = room.get("revision", 1)

    client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "QUEUE_ADD", "payload": {"track_id": "next_atomic_track"}}
    )
    rev_1 = client.get(f"/api/rooms/{room_id}", headers=headers).json().get("revision")

    # Advancing with expected_revision matching current revision succeeds
    adv_res = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "NEXT_TRACK", "expected_revision": rev_1}
    )
    assert adv_res.status_code == 200
    new_rev = adv_res.json()["revision"]
    assert new_rev == rev_1 + 1

    # Stale expected_revision must be rejected with 409 Conflict
    stale_res = client.post(
        f"/api/rooms/{room_id}/command",
        headers=headers,
        json={"action": "NEXT_TRACK", "expected_revision": rev_1}
    )
    assert stale_res.status_code == 409
    assert "revision conflict" in stale_res.json()["detail"].lower()


def test_legacy_queue_reconciliation_database_error_handling():
    """Verify legacy update_queue endpoint propagates unexpected database errors while tolerating unmigrated tables."""
    from unittest.mock import MagicMock

    host_token = get_auth_token(tg_id=90050, username="host_db_err")
    headers = {"Authorization": f"Bearer {host_token}"}

    room = client.post("/api/rooms", headers=headers, json={"name": "DB Err Room", "is_public": True}).json()
    room_id = room["id"]

    # 1. Mock Supabase live mode with an unexpected database failure during room_queue sync
    mock_admin = MagicMock()
    mock_admin.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"id": room_id, "host_id": "any", "is_active": True, "revision": 1}
    ]
    mock_admin.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{}]
    mock_admin.table("room_queue").select.return_value.eq.return_value.is_.return_value.order.return_value.execute.side_effect = Exception("FATAL: connection terminated unexpectedly")

    with patch.object(SupabaseService, "is_live", new_callable=PropertyMock, return_value=True), \
         patch.object(SupabaseService, "get_admin", return_value=mock_admin):
        # Must return 500 and not silently swallow the database failure
        res = client.post(
            f"/api/rooms/{room_id}/queue",
            headers=headers,
            json={"queue": ["track_1"]}
        )
        assert res.status_code == 500
        assert "Database failure" in res.json()["detail"]

    # 2. Mock Supabase live mode where table is missing in schema cache (PGRST204) -> tolerated
    mock_admin2 = MagicMock()
    mock_admin2.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"id": room_id, "host_id": "any", "is_active": True, "revision": 1}
    ]
    mock_admin2.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{}]
    mock_admin2.table("room_queue").select.return_value.eq.return_value.is_.return_value.order.return_value.execute.side_effect = Exception({"code": "PGRST204", "message": "Could not find the 'room_queue' table in the schema cache"})

    with patch.object(SupabaseService, "is_live", new_callable=PropertyMock, return_value=True), \
         patch.object(SupabaseService, "get_admin", return_value=mock_admin2):
        # Tolerates missing table and succeeds
        res_tol = client.post(
            f"/api/rooms/{room_id}/queue",
            headers=headers,
            json={"queue": ["track_1"]}
        )
        assert res_tol.status_code == 200


def test_rpc_concurrency_error_does_not_execute_fallback_writes():
    """Verify that when an RPC raises P0004 or P0003 in Supabase live mode, it raises HTTP 409/400 without falling back."""
    from unittest.mock import MagicMock

    host_token = get_auth_token(tg_id=90055, username="host_live_rpc_test")
    headers = {"Authorization": f"Bearer {host_token}"}

    room = client.post("/api/rooms", headers=headers, json={"name": "RPC Mock Room", "is_public": True}).json()
    room_id = room["id"]

    mock_admin = MagicMock()
    # rooms query
    mock_admin.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"id": room_id, "host_id": "test_host", "is_active": True, "revision": 2}
    ]

    # RPC call raises P0004 Revision Conflict
    mock_admin.rpc.return_value.execute.side_effect = Exception({"code": "P0004", "message": "Revision conflict: expected 1, current 2"})

    with patch.object(SupabaseService, "is_live", new_callable=PropertyMock, return_value=True), \
         patch.object(SupabaseService, "get_admin", return_value=mock_admin):
        # Call command
        res = client.post(
            f"/api/rooms/{room_id}/command",
            headers=headers,
            json={"action": "QUEUE_ADD", "expected_revision": 1, "payload": {"track_id": "track_x"}}
        )
        assert res.status_code == 409
        assert "revision conflict" in res.json()["detail"].lower()
        # Ensure insert on room_queue was NEVER called (fallback was NOT executed)
        assert mock_admin.table("room_queue").insert.call_count == 0
