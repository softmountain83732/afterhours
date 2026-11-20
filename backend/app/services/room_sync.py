"""Authoritative synchronization, clock offset estimation, and queue management for Listening Rooms."""

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
from fastapi import HTTPException, status

from app.db import db
from app.config import get_settings
from app.services.supabase_client import supabase_service

logger = logging.getLogger("afterhours.room_sync")

DEFAULT_SCHEDULED_DELAY_MS = 800
MIN_SCHEDULED_DELAY_MS = 500
MAX_SCHEDULED_DELAY_MS = 1500


def broadcast_room_event(room_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    """Broadcast a low-latency event to Supabase Realtime channel room:{room_id}."""
    if not supabase_service.is_live:
        return
    settings = get_settings()
    if not settings.SUPABASE_URL or "placeholder" in settings.SUPABASE_SERVICE_ROLE_KEY or "localhost" in settings.SUPABASE_URL:
        return

    url = f"{settings.SUPABASE_URL.rstrip('/')}/realtime/v1/api/broadcast"
    headers = {
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "messages": [
            {
                "topic": f"room:{room_id}",
                "event": event_type,
                "payload": payload,
            }
        ]
    }
    try:
        import httpx
        with httpx.Client(timeout=1.5) as client:
            client.post(url, headers=headers, json=body)
    except Exception as e:
        logger.debug("Failed to send Realtime broadcast: %s", e)


def get_current_time_ms() -> int:
    """Return current UTC epoch time in milliseconds."""
    return int(time.time() * 1000)


def compute_clock_sync(client_echo: int) -> Dict[str, Any]:
    """Compute NTP-style server timestamp for client clock offset calibration."""
    server_time = get_current_time_ms()
    return {
        "client_echo": client_echo,
        "server_time_ms": server_time,
        "server_iso": datetime.now(timezone.utc).isoformat(),
    }


def verify_room_permission(
    room: Dict[str, Any],
    member: Optional[Dict[str, Any]],
    action: str,
    user_id: str
) -> bool:
    """
    Validate whether user has permission to perform room action.
    Host + Custom Guest Permissions Model:
      - Host has full authority.
      - DJ: can_control_playback + can_manage_queue + can_invite.
      - Queue Manager: can_manage_queue + can_invite.
      - Moderator: can_moderate + can_invite.
      - Listener: Listen, view, and vote on queue items.
    """
    if user_id == room.get("host_id"):
        return True

    if not member:
        return False

    role = member.get("role", "listener")
    can_control_playback = member.get("can_control_playback", False) or (role in ("host", "dj"))
    can_manage_queue = member.get("can_manage_queue", False) or (role in ("host", "dj", "queue_manager"))
    can_invite = member.get("can_invite", False) or (role in ("host", "dj", "queue_manager", "moderator"))
    can_moderate = member.get("can_moderate", False) or (role in ("host", "moderator"))

    playback_actions = {"PLAY", "PAUSE", "SEEK", "SYNC_PLAYBACK", "CHANGE_TRACK"}
    queue_manage_actions = {"QUEUE_ADD", "QUEUE_REMOVE", "QUEUE_REORDER", "NEXT_TRACK"}
    voting_actions = {"QUEUE_VOTE"}
    invite_actions = {"INVITE"}
    moderate_actions = {"KICK", "PERMISSIONS", "UPDATE_PERMISSIONS"}

    if action in playback_actions:
        return can_control_playback
    if action in queue_manage_actions:
        return can_manage_queue
    if action in voting_actions:
        return True  # Any room member can vote
    if action in invite_actions:
        return can_invite
    if action in moderate_actions:
        return can_moderate

    return False


def get_room_queue_items(room_id: str) -> List[Dict[str, Any]]:
    """
    Fetch pending queue items ordered by (vote_count DESC, position ASC).
    Single source of truth is normalized room_queue, falling back to legacy rooms.queue if table not yet created.
    """
    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        try:
            res = (
                admin.table("room_queue")
                .select("*, tracks(*, artists(name), albums(title, artwork_url, artwork_thumb_url)), profiles(username, display_name)")
                .eq("room_id", room_id)
                .is_("played_at", "null")
                .order("vote_count", desc=True)
                .order("position", desc=False)
                .execute()
            )
            if res.data is not None:
                return res.data
        except Exception as exc:
            logger.debug("room_queue table query failed (migration pending?): %s", exc)

        # Fallback to legacy queue in rooms table
        try:
            r_res = admin.table("rooms").select("queue").eq("id", room_id).execute()
            if r_res.data and r_res.data[0].get("queue"):
                legacy_ids = r_res.data[0]["queue"]
                return [
                    {
                        "id": f"legacy_{i}",
                        "room_id": room_id,
                        "track_id": tid,
                        "position": i,
                        "vote_count": 0,
                        "played_at": None,
                    }
                    for i, tid in enumerate(legacy_ids)
                ]
        except Exception:
            pass
        return []

    # In-memory store
    items = [
        item for item in db.room_queues.get(room_id, [])
        if item.get("played_at") is None
    ]
    # Sort by vote_count DESC, position ASC
    items.sort(key=lambda x: (-x.get("vote_count", 0), x.get("position", 0)))
    return items


def record_audit_log(room_id: str, user_id: str, action: str, revision: int, payload: Dict[str, Any]) -> None:
    """Record an audit log entry for room state mutations."""
    entry = {
        "id": str(uuid.uuid4()),
        "room_id": room_id,
        "user_id": user_id,
        "action": action,
        "revision": revision,
        "payload": payload,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if supabase_service.is_live:
        try:
            admin = supabase_service.get_admin()
            admin.table("room_sync_audit_log").insert(entry).execute()
        except Exception as exc:
            logger.warning("Failed to write audit log to Supabase: %s", exc)
    else:
        db.room_sync_audit_log.append(entry)


def classify_rpc_error(exc: Exception) -> Tuple[bool, Optional[HTTPException]]:
    """
    Classifies a Supabase/PostgREST RPC exception.

    Returns:
        (is_missing_rpc, http_exception)
        - is_missing_rpc == True: Migration 004 is unapplied. Safe to fall back.
        - is_missing_rpc == False and http_exception is not None: Known business/concurrency error to raise.
        - is_missing_rpc == False and http_exception is None: Unknown/database/network error to propagate.
    """
    code = getattr(exc, "code", None)
    if code is None and hasattr(exc, "info") and isinstance(exc.info, dict):
        code = exc.info.get("code")
    if code is None and len(getattr(exc, "args", ())) > 0 and isinstance(exc.args[0], dict):
        code = exc.args[0].get("code")

    msg = getattr(exc, "message", None)
    if not msg and len(getattr(exc, "args", ())) > 0 and isinstance(exc.args[0], dict):
        msg = exc.args[0].get("message")
    if not msg:
        msg = str(exc)

    details = getattr(exc, "details", None)
    if not details and len(getattr(exc, "args", ())) > 0 and isinstance(exc.args[0], dict):
        details = exc.args[0].get("details")
    details_str = str(details or "")

    combined_text = f"{code or ''} {msg} {details_str}".lower()

    # 1. Genuine missing function/schema-cache error (allow fallback)
    if code in ("PGRST202", "42883") or "could not find the function" in combined_text or ("schema cache" in combined_text and "function" in combined_text):
        return True, None

    # 2. Concurrency Conflict: P0004
    if code == "P0004" or "p0004" in combined_text or "revision conflict" in combined_text:
        return False, HTTPException(status_code=status.HTTP_409_CONFLICT, detail="State revision conflict")

    # 3. Room Membership / Cross-Room Authorization: 42501
    if code == "42501" or "42501" in combined_text or "does not belong to this room" in combined_text:
        return False, HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Queue item does not belong to this room")

    # 4. Inactive Room: P0003
    if code == "P0003" or "p0003" in combined_text or "room is inactive" in combined_text:
        return False, HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Room is inactive")

    # 5. Resource Not Found: P0002
    if code == "P0002" or "p0002" in combined_text or "not found" in combined_text:
        return False, HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")

    # 6. Already Played Item: P0006
    if code == "P0006" or "p0006" in combined_text or "cannot vote on played queue item" in combined_text:
        return False, HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot vote on a played queue item")

    # 7. Invalid Vote Value: P0005
    if code == "P0005" or "p0005" in combined_text or "vote must be 1 or -1" in combined_text:
        return False, HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vote must be 1 or -1")

    # 8. All other unknown, DB failure, network errors -> must propagate, NEVER fall back!
    return False, None


def execute_room_command(
    room_id: str,
    user_id: str,
    action: str,
    payload: Dict[str, Any],
    expected_revision: Optional[int] = None
) -> Dict[str, Any]:
    """
    Execute an authoritative room sync command.
    Enforces server-side permissions, monotonic revisions, dual-write to legacy queue,
    scheduled playback calculation, and audit logging.
    """
    action_upper = action.upper()

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        r_res = admin.table("rooms").select("*").eq("id", room_id).execute()
        if not r_res.data or not r_res.data[0].get("is_active"):
            raise HTTPException(status_code=404, detail="Room not found or no longer active")
        room = r_res.data[0]

        # Member permissions
        m_res = admin.table("room_members").select("*").eq("room_id", room_id).eq("user_id", user_id).execute()
        member = m_res.data[0] if m_res.data else None

        if not verify_room_permission(room, member, action_upper, user_id):
            raise HTTPException(status_code=403, detail=f"Permission denied for action: {action_upper}")

        current_rev = room.get("revision", 1)
        if expected_revision is not None and expected_revision != current_rev:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"State revision conflict. Expected {current_rev}, got {expected_revision}"
            )

        new_rev = current_rev + 1
        now_dt = datetime.now(timezone.utc)
        now_ms = get_current_time_ms()
        is_duplicate = False
        room_updates: Dict[str, Any] = {
            "revision": new_rev,
            "updated_by": user_id,
            "server_timestamp": now_dt.isoformat(),
            "updated_at": now_dt.isoformat(),
        }

        # Determine if immediate 0ms playback requested
        req_delay = payload.get("scheduled_delay_ms")
        is_immediate = (req_delay == 0)

        # Handle specific actions
        if action_upper in ("PLAY", "CHANGE_TRACK"):
            track_id = payload.get("track_id")
            if action_upper == "CHANGE_TRACK" and not track_id:
                raise HTTPException(status_code=400, detail="track_id required for CHANGE_TRACK")

            # Validate track exists in database
            if track_id:
                t_check = admin.table("tracks").select("id").eq("id", track_id).execute()
                if not t_check.data:
                    raise HTTPException(status_code=404, detail="Track not found")

            if is_immediate:
                scheduled_start_ms = None
            else:
                delay = req_delay if req_delay is not None else DEFAULT_SCHEDULED_DELAY_MS
                delay = max(MIN_SCHEDULED_DELAY_MS, min(MAX_SCHEDULED_DELAY_MS, delay))
                scheduled_start_ms = now_ms + delay

            pos_ms = 0 if action_upper == "CHANGE_TRACK" else payload.get("position_ms", room.get("playback_position_ms", 0))
            rate = float(payload.get("playback_rate", 1.0))

            room_updates["is_playing"] = True
            room_updates["playback_position_ms"] = pos_ms
            room_updates["playback_rate"] = rate
            room_updates["scheduled_start_ms"] = scheduled_start_ms
            room_updates["playback_updated_at"] = now_dt.isoformat()

            if track_id:
                room_updates["current_track_id"] = track_id

        elif action_upper == "PAUSE":
            pos_ms = payload.get("position_ms", room.get("playback_position_ms", 0))
            room_updates["is_playing"] = False
            room_updates["playback_position_ms"] = pos_ms
            room_updates["scheduled_start_ms"] = None
            room_updates["playback_updated_at"] = now_dt.isoformat()

        elif action_upper == "SEEK":
            pos_ms = payload.get("position_ms", 0)
            room_updates["playback_position_ms"] = pos_ms
            room_updates["playback_updated_at"] = now_dt.isoformat()
            if is_immediate or not room.get("is_playing"):
                room_updates["scheduled_start_ms"] = None
            else:
                delay = req_delay if req_delay is not None else DEFAULT_SCHEDULED_DELAY_MS
                delay = max(MIN_SCHEDULED_DELAY_MS, min(MAX_SCHEDULED_DELAY_MS, delay))
                room_updates["scheduled_start_ms"] = now_ms + delay

        elif action_upper == "QUEUE_ADD":
            track_id = payload.get("track_id")
            if not track_id:
                raise HTTPException(status_code=400, detail="track_id required")

            # Check if this track is already pending/unplayed in the queue (deduplicate against retry replay)
            existing_unplayed = admin.table("room_queue").select("id").eq("room_id", room_id).eq("track_id", track_id).is_("played_at", "null").limit(1).execute()
            if existing_unplayed.data:
                # Already in queue: idempotent success without duplicating
                pending_items = get_room_queue_items(room_id)
                room_updates["queue"] = [i["track_id"] for i in pending_items]
                rpc_handled = True
                is_duplicate = True
            else:
                rpc_handled = False
                try:
                    rpc_res = admin.rpc("add_room_queue_item_atomic", {
                        "p_room_id": room_id,
                        "p_track_id": track_id,
                        "p_user_id": user_id,
                        "p_expected_revision": expected_revision
                    }).execute()
                    if rpc_res.data:
                        rpc_handled = True
                        room_updates["queue"] = rpc_res.data.get("queue", [])
                        new_rev = rpc_res.data.get("revision", new_rev)
                except Exception as exc:
                    can_fallback, http_exc = classify_rpc_error(exc)
                    if http_exc:
                        raise http_exc
                    if not can_fallback:
                        raise

            if not rpc_handled:
                # Get max position in room_queue
                pos_res = admin.table("room_queue").select("position").eq("room_id", room_id).is_("played_at", "null").order("position", desc=True).limit(1).execute()
                next_pos = (pos_res.data[0]["position"] + 1) if pos_res.data else 0

                admin.table("room_queue").insert({
                    "room_id": room_id,
                    "track_id": track_id,
                    "added_by": user_id,
                    "position": next_pos,
                    "vote_count": 0,
                }).execute()

                # Transactional dual-write to legacy rooms.queue
                current_queue = room.get("queue") or []
                updated_queue = list(current_queue) + [track_id]
                room_updates["queue"] = updated_queue

        elif action_upper == "QUEUE_REMOVE":
            queue_item_id = payload.get("queue_item_id")
            track_id = payload.get("track_id")
            rpc_handled = False

            if queue_item_id:
                # Verify queue item belongs to this room
                item_res = admin.table("room_queue").select("id, room_id").eq("id", queue_item_id).execute()
                if not item_res.data:
                    raise HTTPException(status_code=404, detail="Queue item not found")
                if str(item_res.data[0].get("room_id")) != str(room_id):
                    raise HTTPException(status_code=403, detail="Queue item does not belong to this room")

                try:
                    rpc_res = admin.rpc("remove_room_queue_item_atomic", {
                        "p_room_id": room_id,
                        "p_queue_item_id": queue_item_id,
                        "p_user_id": user_id,
                        "p_expected_revision": expected_revision
                    }).execute()
                    if rpc_res.data:
                        rpc_handled = True
                        room_updates["queue"] = rpc_res.data.get("queue", [])
                        new_rev = rpc_res.data.get("revision", new_rev)
                except Exception as exc:
                    can_fallback, http_exc = classify_rpc_error(exc)
                    if http_exc:
                        raise http_exc
                    if not can_fallback:
                        raise

                if not rpc_handled:
                    admin.table("room_queue").delete().eq("id", queue_item_id).eq("room_id", room_id).execute()
            elif track_id:
                admin.table("room_queue").delete().eq("track_id", track_id).eq("room_id", room_id).execute()

            if not rpc_handled:
                # Dual-write legacy queue update
                pending_items = get_room_queue_items(room_id)
                room_updates["queue"] = [i["track_id"] for i in pending_items]

        elif action_upper == "QUEUE_VOTE":
            queue_item_id = payload.get("queue_item_id")
            if not queue_item_id:
                raise HTTPException(status_code=400, detail="queue_item_id required")
            vote_val = payload.get("vote", 1)
            if vote_val not in (1, -1):
                raise HTTPException(status_code=400, detail="Vote must be 1 or -1")

            # Verify queue item belongs to this room and is unplayed
            item_res = admin.table("room_queue").select("id, room_id, played_at").eq("id", queue_item_id).execute()
            if not item_res.data:
                raise HTTPException(status_code=404, detail="Queue item not found")
            if str(item_res.data[0].get("room_id")) != str(room_id):
                raise HTTPException(status_code=403, detail="Queue item does not belong to this room")
            if item_res.data[0].get("played_at") is not None:
                raise HTTPException(status_code=400, detail="Cannot vote on a played queue item")

            rpc_handled = False
            try:
                rpc_res = admin.rpc("vote_room_queue_item_atomic", {
                    "p_room_id": room_id,
                    "p_queue_item_id": queue_item_id,
                    "p_user_id": user_id,
                    "p_vote": vote_val,
                    "p_expected_revision": expected_revision
                }).execute()
                if rpc_res.data:
                    rpc_handled = True
                    room_updates["queue"] = rpc_res.data.get("queue", [])
                    new_rev = rpc_res.data.get("revision", new_rev)
            except Exception as exc:
                can_fallback, http_exc = classify_rpc_error(exc)
                if http_exc:
                    raise http_exc
                if not can_fallback:
                    raise

            if not rpc_handled:
                admin.table("room_queue_votes").upsert({
                    "queue_item_id": queue_item_id,
                    "user_id": user_id,
                    "vote": vote_val,
                }, on_conflict="queue_item_id,user_id").execute()

                # Recalculate vote count
                votes_res = admin.table("room_queue_votes").select("vote").eq("queue_item_id", queue_item_id).execute()
                total_votes = sum(v["vote"] for v in (votes_res.data or []))
                admin.table("room_queue").update({"vote_count": total_votes}).eq("id", queue_item_id).execute()

                # Dual-write reordered legacy queue
                pending_items = get_room_queue_items(room_id)
                room_updates["queue"] = [i["track_id"] for i in pending_items]

        elif action_upper == "NEXT_TRACK":
            if is_immediate:
                delay = 0
            else:
                delay = payload.get("scheduled_delay_ms", DEFAULT_SCHEDULED_DELAY_MS)
                delay = max(MIN_SCHEDULED_DELAY_MS, min(MAX_SCHEDULED_DELAY_MS, delay))

            rpc_handled = False
            try:
                rpc_res = admin.rpc("next_room_track_atomic", {
                    "p_room_id": room_id,
                    "p_user_id": user_id,
                    "p_scheduled_delay_ms": delay,
                    "p_expected_revision": expected_revision
                }).execute()
                if rpc_res.data:
                    rpc_handled = True
                    d = rpc_res.data
                    room_updates["queue"] = d.get("queue", [])
                    new_rev = d.get("revision", new_rev)
                    has_next = d.get("has_next", False)
                    if has_next and d.get("current_track_id"):
                        room_updates["current_track_id"] = d.get("current_track_id")
                        room_updates["is_playing"] = True
                        room_updates["scheduled_start_ms"] = None if is_immediate else d.get("scheduled_start_ms")
                        room_updates["playback_position_ms"] = 0
                        room_updates["playback_updated_at"] = now_dt.isoformat()
                    else:
                        # Queue is empty: preserve existing current_track_id, pause playback gracefully
                        room_updates["is_playing"] = False
                        room_updates["scheduled_start_ms"] = None
                        room_updates["playback_updated_at"] = now_dt.isoformat()
            except Exception as exc:
                can_fallback, http_exc = classify_rpc_error(exc)
                if http_exc:
                    raise http_exc
                if not can_fallback:
                    raise

            if not rpc_handled:
                # Advance to next track from normalized queue (fallback)
                pending_items = get_room_queue_items(room_id)
                if pending_items:
                    next_item = pending_items[0]
                    admin.table("room_queue").update({"played_at": now_dt.isoformat()}).eq("id", next_item["id"]).execute()
                    room_updates["current_track_id"] = next_item["track_id"]
                    room_updates["playback_position_ms"] = 0
                    room_updates["scheduled_start_ms"] = None if is_immediate else (now_ms + delay)
                    room_updates["is_playing"] = True
                    room_updates["playback_updated_at"] = now_dt.isoformat()

                    # Dual-write remaining legacy queue
                    room_updates["queue"] = [i["track_id"] for i in pending_items[1:]]
                else:
                    # Queue is empty in fallback: pause playback gracefully without wiping current track
                    room_updates["is_playing"] = False
                    room_updates["scheduled_start_ms"] = None
                    room_updates["playback_updated_at"] = now_dt.isoformat()

        # Commit room updates
        try:
            res = admin.table("rooms").update(room_updates).eq("id", room_id).execute()
        except Exception as exc:
            if "PGRST204" in str(exc) or "column" in str(exc).lower():
                # Filter to legacy columns if migration 003 columns not yet created in DB
                legacy_keys = {
                    "current_track_id", "playback_position_ms", "is_playing", "playback_updated_at", "queue", "updated_at"
                }
                legacy_updates = {k: v for k, v in room_updates.items() if k in legacy_keys}
                res = admin.table("rooms").update(legacy_updates).eq("id", room_id).execute()
            else:
                raise
        updated_room = res.data[0] if res.data else {**room, **room_updates}

        record_audit_log(room_id, user_id, action_upper, new_rev, payload)
        if action_upper in ("PLAY", "PAUSE", "SEEK", "NEXT_TRACK", "CHANGE_TRACK"):
            broadcast_room_event(room_id, "playback_sync", {"action": action_upper, "room": updated_room, "revision": new_rev})
        elif action_upper in ("QUEUE_ADD", "QUEUE_REMOVE", "QUEUE_VOTE"):
            broadcast_room_event(room_id, "queue_updated", {"action": action_upper, "queue": updated_room.get("queue", []), "revision": new_rev})
        ret = {
            "success": True,
            "action": action_upper,
            "revision": new_rev,
            "server_timestamp": now_dt.isoformat(),
            "scheduled_start_ms": room_updates.get("scheduled_start_ms"),
            "room": updated_room,
        }
        if is_duplicate:
            ret["is_duplicate"] = True
        return ret

    # In-memory development / unit test implementation
    room = db.rooms.get(room_id)
    if not room or not room.get("is_active"):
        raise HTTPException(status_code=404, detail="Room not found or no longer active")

    members_map = db.room_members.get(room_id, {})
    member = members_map.get(user_id)

    if not verify_room_permission(room, member, action_upper, user_id):
        raise HTTPException(status_code=403, detail=f"Permission denied for action: {action_upper}")

    current_rev = room.get("revision", 1)
    if expected_revision is not None and expected_revision != current_rev:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"State revision conflict. Expected {current_rev}, got {expected_revision}"
        )

    new_rev = current_rev + 1
    now_dt = datetime.now(timezone.utc)
    now_ms = get_current_time_ms()
    now_iso = now_dt.isoformat()
    is_duplicate = False

    room["revision"] = new_rev
    room["updated_by"] = user_id
    room["server_timestamp"] = now_iso
    room["updated_at"] = now_iso

    queue_list = db.room_queues.setdefault(room_id, [])

    members_map = db.room_members.get(room_id, {})
    req_delay = payload.get("scheduled_delay_ms")
    is_immediate = (req_delay == 0)

    if action_upper in ("PLAY", "CHANGE_TRACK"):
        track_id = payload.get("track_id")
        if action_upper == "CHANGE_TRACK" and not track_id:
            raise HTTPException(status_code=400, detail="track_id required for CHANGE_TRACK")

        # Validate track exists in mock DB
        if track_id and track_id not in db.tracks:
            raise HTTPException(status_code=404, detail="Track not found")

        if is_immediate:
            scheduled_start_ms = None
        else:
            delay = req_delay if req_delay is not None else DEFAULT_SCHEDULED_DELAY_MS
            delay = max(MIN_SCHEDULED_DELAY_MS, min(MAX_SCHEDULED_DELAY_MS, delay))
            scheduled_start_ms = now_ms + delay

        pos_ms = 0 if action_upper == "CHANGE_TRACK" else payload.get("position_ms", room.get("playback_position_ms", 0))
        rate = float(payload.get("playback_rate", 1.0))

        room["is_playing"] = True
        room["playback_position_ms"] = pos_ms
        room["playback_rate"] = rate
        room["scheduled_start_ms"] = scheduled_start_ms
        room["playback_updated_at"] = now_iso

        if track_id:
            room["current_track_id"] = track_id

    elif action_upper == "PAUSE":
        pos_ms = payload.get("position_ms", room.get("playback_position_ms", 0))
        room["is_playing"] = False
        room["playback_position_ms"] = pos_ms
        room["scheduled_start_ms"] = None
        room["playback_updated_at"] = now_iso

    elif action_upper == "SEEK":
        pos_ms = payload.get("position_ms", 0)
        room["playback_position_ms"] = pos_ms
        room["playback_updated_at"] = now_iso
        if is_immediate or not room.get("is_playing"):
            room["scheduled_start_ms"] = None
        else:
            delay = req_delay if req_delay is not None else DEFAULT_SCHEDULED_DELAY_MS
            delay = max(MIN_SCHEDULED_DELAY_MS, min(MAX_SCHEDULED_DELAY_MS, delay))
            room["scheduled_start_ms"] = now_ms + delay

    elif action_upper == "QUEUE_ADD":
        track_id = payload.get("track_id")
        if not track_id:
            raise HTTPException(status_code=400, detail="track_id required")

        # Deduplicate if already pending/unplayed in queue
        already_in_queue = any(i.get("track_id") == track_id and i.get("played_at") is None for i in queue_list)
        if already_in_queue:
            is_duplicate = True
        else:
            next_pos = max([item.get("position", 0) for item in queue_list], default=-1) + 1
            item_id = str(uuid.uuid4())
            queue_item = {
                "id": item_id,
                "room_id": room_id,
                "track_id": track_id,
                "added_by": user_id,
                "position": next_pos,
                "vote_count": 0,
                "played_at": None,
                "created_at": now_iso,
            }
            queue_list.append(queue_item)

        # Dual-write legacy queue
        room["queue"] = [i["track_id"] for i in get_room_queue_items(room_id)]

    elif action_upper == "QUEUE_REMOVE":
        queue_item_id = payload.get("queue_item_id")
        track_id = payload.get("track_id")
        if queue_item_id:
            matching_item = next((i for i in queue_list if i.get("id") == queue_item_id), None)
            if not matching_item:
                exists_elsewhere = any(
                    any(i.get("id") == queue_item_id for i in other_q)
                    for other_rid, other_q in db.room_queues.items()
                    if other_rid != room_id
                )
                if exists_elsewhere:
                    raise HTTPException(status_code=403, detail="Queue item does not belong to this room")
                raise HTTPException(status_code=404, detail="Queue item not found")
            db.room_queues[room_id] = [i for i in queue_list if i["id"] != queue_item_id]
        elif track_id:
            db.room_queues[room_id] = [i for i in queue_list if i["track_id"] != track_id]

        # Dual-write legacy queue
        room["queue"] = [i["track_id"] for i in get_room_queue_items(room_id)]

    elif action_upper == "QUEUE_VOTE":
        queue_item_id = payload.get("queue_item_id")
        if not queue_item_id:
            raise HTTPException(status_code=400, detail="queue_item_id required")
        vote_val = payload.get("vote", 1)
        if vote_val not in (1, -1):
            raise HTTPException(status_code=400, detail="Vote must be 1 or -1")

        matching_item = next((i for i in queue_list if i.get("id") == queue_item_id), None)
        if not matching_item:
            exists_elsewhere = any(
                any(i.get("id") == queue_item_id for i in other_q)
                for other_rid, other_q in db.room_queues.items()
                if other_rid != room_id
            )
            if exists_elsewhere:
                raise HTTPException(status_code=403, detail="Queue item does not belong to this room")
            raise HTTPException(status_code=404, detail="Queue item not found")

        if matching_item.get("played_at") is not None:
            raise HTTPException(status_code=400, detail="Cannot vote on a played queue item")

        votes_dict = db.room_queue_votes.setdefault(queue_item_id, {})
        votes_dict[user_id] = vote_val

        # Recalculate vote count on item
        total_votes = sum(votes_dict.values())
        for item in queue_list:
            if item["id"] == queue_item_id:
                item["vote_count"] = total_votes

        # Dual-write legacy queue with newly sorted order
        room["queue"] = [i["track_id"] for i in get_room_queue_items(room_id)]

    elif action_upper == "NEXT_TRACK":
        pending_items = get_room_queue_items(room_id)
        if pending_items:
            next_item = pending_items[0]
            next_item["played_at"] = now_iso
            room["current_track_id"] = next_item["track_id"]
            room["playback_position_ms"] = 0
            delay = payload.get("scheduled_delay_ms", DEFAULT_SCHEDULED_DELAY_MS)
            delay = max(MIN_SCHEDULED_DELAY_MS, min(MAX_SCHEDULED_DELAY_MS, delay))
            room["scheduled_start_ms"] = None if is_immediate else (now_ms + delay)
            room["is_playing"] = True
            room["playback_updated_at"] = now_iso

            # Dual-write legacy queue
            room["queue"] = [i["track_id"] for i in get_room_queue_items(room_id)]
        else:
            # Queue is empty: preserve current_track_id, pause playback safely
            room["is_playing"] = False
            room["scheduled_start_ms"] = None
            room["playback_updated_at"] = now_iso

    record_audit_log(room_id, user_id, action_upper, new_rev, payload)
    ret = {
        "success": True,
        "action": action_upper,
        "revision": new_rev,
        "server_timestamp": now_iso,
        "scheduled_start_ms": room.get("scheduled_start_ms"),
        "room": room,
    }
    if is_duplicate:
        ret["is_duplicate"] = True
    return ret
