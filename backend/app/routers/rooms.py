"""Listening Rooms and playback synchronization endpoints."""

import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth import get_current_user, get_optional_user
from app.db import db
from app.routers.tracks import format_track
from app.services.supabase_client import supabase_service
from app.services.room_sync import (
    compute_clock_sync,
    execute_room_command,
    get_room_queue_items,
    get_current_time_ms,
    broadcast_room_event,
)

logger = logging.getLogger("afterhours.rooms")

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


class RoomCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    is_public: bool = True
    max_members: int = Field(30, ge=2, le=100)


class RoomUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    is_public: Optional[bool] = None


class PlaybackSyncRequest(BaseModel):
    current_track_id: Optional[str] = None
    playback_position_ms: int = Field(..., ge=0)
    is_playing: bool


class QueueUpdateRequest(BaseModel):
    queue: List[str]


class PermissionsUpdateRequest(BaseModel):
    can_control_playback: Optional[bool] = None
    can_manage_queue: Optional[bool] = None
    can_invite: Optional[bool] = None
    can_moderate: Optional[bool] = None
    role: Optional[str] = None


class RoomCommandRequest(BaseModel):
    action: str = Field(..., min_length=1)
    expected_revision: Optional[int] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class TransferHostRequest(BaseModel):
    new_host_id: str


class ChatMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)


@router.get("/sync/clock")
async def sync_clock(client_echo: int = Query(0)):
    """NTP-style clock sync endpoint returning authoritative server timestamp."""
    return compute_clock_sync(client_echo)


@router.post("/{room_id}/command")
async def dispatch_room_command(
    room_id: str,
    req: RoomCommandRequest,
    user: dict = Depends(get_current_user)
):
    """Server-authoritative command dispatcher for Listening Room state mutations."""
    return execute_room_command(
        room_id=room_id,
        user_id=user["sub"],
        action=req.action,
        payload=req.payload,
        expected_revision=req.expected_revision,
    )


@router.get("/{room_id}/queue")
async def get_normalized_queue(room_id: str):
    """Get normalized room queue items ordered by vote count and position."""
    return get_room_queue_items(room_id)


@router.get("")
async def list_global_rooms(
    q: Optional[str] = Query(None, description="Search rooms by name or host")
):
    """List live public rooms (Global Rooms)."""
    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        rooms_res = admin.table("rooms").select(
            "*, profiles!rooms_host_id_fkey(display_name, username), tracks(*, artists(name), albums(title, artwork_url, artwork_thumb_url), track_artwork(original_url, thumb_url))"
        ).eq("is_public", True).eq("is_active", True).order("created_at", desc=True).execute()

        rooms = rooms_res.data or []
        result = []
        for r in rooms:
            host_prof = r.get("profiles") or {}
            host_name = host_prof.get("display_name") or host_prof.get("username", "Host")
            raw_t = r.get("tracks")
            current_track = format_track(raw_t) if raw_t else None

            # Count members
            mem_count_res = admin.table("room_members").select("id", count="exact").eq("room_id", r["id"]).execute()
            listener_count = (mem_count_res.count or len(mem_count_res.data or [])) + 1

            if q and q.strip():
                term = q.strip().lower()
                if term not in r.get("name", "").lower() and term not in host_name.lower():
                    continue

            result.append({
                "id": r["id"],
                "name": r["name"],
                "host_id": r["host_id"],
                "host_name": host_name,
                "listener_count": listener_count,
                "max_members": r.get("max_members", 30),
                "is_playing": r.get("is_playing", False),
                "current_track": current_track,
                "created_at": r["created_at"],
            })
        return result

    public_rooms = [
        r for r in db.rooms.values()
        if r.get("is_public") and r.get("is_active")
    ]

    if q and q.strip():
        term = q.strip().lower()
        public_rooms = [
            r for r in public_rooms
            if term in r.get("name", "").lower() or term in r.get("host_name", "").lower()
        ]

    result = []
    for r in public_rooms:
        current_track = db.tracks.get(r.get("current_track_id")) if r.get("current_track_id") else None
        members = db.room_members.get(r["id"], {})
        listener_count = len(members) + 1

        result.append({
            "id": r["id"],
            "name": r["name"],
            "host_id": r["host_id"],
            "host_name": r.get("host_name", "Host"),
            "listener_count": listener_count,
            "max_members": r.get("max_members", 30),
            "is_playing": r.get("is_playing", False),
            "current_track": current_track,
            "created_at": r["created_at"],
        })

    return result


@router.post("")
async def create_room(req: RoomCreateRequest, user: dict = Depends(get_current_user)):
    """Create a new public or private room."""
    if user.get("is_guest"):
        raise HTTPException(status_code=403, detail="Guests cannot create rooms")

    user_id = user["sub"]
    room_id = str(uuid.uuid4())
    invite_token = secrets.token_urlsafe(16)

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        prof_res = admin.table("profiles").select("display_name, username").eq("id", user_id).execute()
        host_name = user.get("username", "Host")
        if prof_res.data:
            host_name = prof_res.data[0].get("display_name") or prof_res.data[0].get("username") or host_name

        # First available public track
        t_res = admin.table("tracks").select("id").eq("is_private", False).limit(1).execute()
        first_track_id = t_res.data[0]["id"] if t_res.data else None

        now_iso = datetime.now(timezone.utc).isoformat()
        room_data = {
            "id": room_id,
            "name": req.name.strip(),
            "host_id": user_id,
            "is_public": req.is_public,
            "is_active": True,
            "invite_token": invite_token,
            "max_members": req.max_members,
            "current_track_id": first_track_id,
            "playback_position_ms": 0,
            "is_playing": False,
            "playback_updated_at": now_iso,
            "revision": 1,
            "playback_rate": 1.0,
            "server_timestamp": now_iso,
            "scheduled_start_ms": None,
            "updated_by": user_id,
            "queue": [first_track_id] if first_track_id else [],
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        try:
            r_ins = admin.table("rooms").insert(room_data).execute()
        except Exception as exc:
            if "PGRST204" in str(exc) or "column" in str(exc).lower():
                legacy_keys = {
                    "id", "name", "host_id", "is_public", "is_active", "invite_token",
                    "max_members", "current_track_id", "playback_position_ms", "is_playing",
                    "playback_updated_at", "queue", "created_at", "updated_at"
                }
                legacy_room_data = {k: v for k, v in room_data.items() if k in legacy_keys}
                r_ins = admin.table("rooms").insert(legacy_room_data).execute()
            else:
                raise
        created_room = r_ins.data[0] if r_ins.data else room_data
        created_room["host_name"] = host_name

        if first_track_id:
            try:
                admin.table("room_queue").insert({
                    "room_id": room_id,
                    "track_id": first_track_id,
                    "added_by": user_id,
                    "position": 0,
                    "vote_count": 0,
                }).execute()
            except Exception:
                pass

        # Register creator as host in room_members
        try:
            admin.table("room_members").upsert({
                "room_id": room_id,
                "user_id": user_id,
                "role": "host",
                "can_control_playback": True,
                "can_manage_queue": True,
                "can_invite": True,
                "can_moderate": True,
            }, on_conflict="room_id,user_id").execute()
        except Exception:
            try:
                admin.table("room_members").upsert({
                    "room_id": room_id,
                    "user_id": user_id,
                    "can_control_playback": True,
                    "can_manage_queue": True,
                }, on_conflict="room_id,user_id").execute()
            except Exception:
                pass

        db.rooms[room_id] = created_room
        db.room_members[room_id] = {
            user_id: {
                "user_id": user_id,
                "role": "host",
                "can_control_playback": True,
                "can_manage_queue": True,
                "can_invite": True,
                "can_moderate": True,
                "joined_at": now_iso,
            }
        }
        db.room_messages[room_id] = []
        db.room_queues[room_id] = [
            {
                "id": str(uuid.uuid4()),
                "room_id": room_id,
                "track_id": first_track_id,
                "added_by": user_id,
                "position": 0,
                "vote_count": 0,
                "played_at": None,
                "created_at": now_iso,
            }
        ] if first_track_id else []
        return created_room

    profile = db.profiles.get(user_id, {})
    host_name = profile.get("display_name") or user.get("username", "Host")
    first_track_id = next(iter(db.tracks.keys()), None)
    now_iso = datetime.now(timezone.utc).isoformat()

    room = {
        "id": room_id,
        "name": req.name.strip(),
        "host_id": user_id,
        "host_name": host_name,
        "is_public": req.is_public,
        "is_active": True,
        "invite_token": invite_token,
        "max_members": req.max_members,
        "current_track_id": first_track_id,
        "playback_position_ms": 0,
        "is_playing": False,
        "playback_updated_at": now_iso,
        "revision": 1,
        "playback_rate": 1.0,
        "server_timestamp": now_iso,
        "scheduled_start_ms": None,
        "updated_by": user_id,
        "queue": [first_track_id] if first_track_id else [],
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    db.rooms[room_id] = room
    db.room_members[room_id] = {
        user_id: {
            "user_id": user_id,
            "role": "host",
            "can_control_playback": True,
            "can_manage_queue": True,
            "can_invite": True,
            "can_moderate": True,
            "joined_at": now_iso,
        }
    }
    db.room_messages[room_id] = []
    db.room_queues[room_id] = [
        {
            "id": str(uuid.uuid4()),
            "room_id": room_id,
            "track_id": first_track_id,
            "added_by": user_id,
            "position": 0,
            "vote_count": 0,
            "played_at": None,
            "created_at": now_iso,
        }
    ] if first_track_id else []

    return room


@router.get("/{room_id}")
async def get_room(
    room_id: str,
    invite_token: Optional[str] = Query(None),
    user: Optional[dict] = Depends(get_optional_user)
):
    """Get full room details, current track, queue, and member list."""
    user_id = user.get("sub") if user else None

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        r_res = admin.table("rooms").select(
            "*, profiles!rooms_host_id_fkey(display_name, username), tracks(*, artists(name), albums(title, artwork_url, artwork_thumb_url), track_artwork(original_url, thumb_url))"
        ).eq("id", room_id).execute()

        if not r_res.data:
            raise HTTPException(status_code=404, detail="Room not found or no longer active")
        room = r_res.data[0]
        if not room.get("is_active"):
            raise HTTPException(status_code=404, detail="Room not found or no longer active")

        is_host = user_id == room.get("host_id")

        # Members
        try:
            mem_res = admin.table("room_members").select(
                "user_id, role, can_control_playback, can_manage_queue, can_invite, can_moderate, joined_at, profiles(username, display_name, avatar_url, is_guest)"
            ).eq("room_id", room_id).execute()
        except Exception:
            mem_res = admin.table("room_members").select(
                "user_id, can_control_playback, can_manage_queue, joined_at, profiles(username, display_name, avatar_url, is_guest)"
            ).eq("room_id", room_id).execute()
        members_data = mem_res.data or []
        is_member = any(m["user_id"] == user_id for m in members_data)

        # Private access check
        if not room.get("is_public") and not (is_host or is_member):
            if not invite_token or invite_token != room.get("invite_token"):
                raise HTTPException(status_code=403, detail="Valid invite link required to view private room")

        # Tracks in queue (from normalized room_queue single source of truth)
        normalized_queue = get_room_queue_items(room_id)
        queue_tracks = []
        if normalized_queue:
            for item in normalized_queue:
                t = item.get("tracks")
                if t:
                    queue_tracks.append(format_track(t))
        elif room.get("queue"):
            # Fallback for unmigrated legacy queue
            queue_ids = room.get("queue") or []
            qt_res = admin.table("tracks").select(
                "*, artists(name), albums(title, artwork_url, artwork_thumb_url), track_artwork(original_url, thumb_url)"
            ).in_("id", queue_ids).execute()
            by_id = {t["id"]: format_track(t) for t in (qt_res.data or [])}
            for qid in queue_ids:
                if qid in by_id:
                    queue_tracks.append(by_id[qid])

        raw_curr = room.get("tracks")
        current_track = format_track(raw_curr) if raw_curr else None

        # Estimated playback position
        est_position_ms = room.get("playback_position_ms", 0)
        if room.get("is_playing") and room.get("playback_updated_at"):
            try:
                updated_at = datetime.fromisoformat(room["playback_updated_at"])
                delta = (datetime.now(timezone.utc) - updated_at).total_seconds() * 1000
                est_position_ms += int(delta)
            except Exception:
                pass

        member_list = []
        for m in members_data:
            p = m.get("profiles") or {}
            member_list.append({
                "user_id": m["user_id"],
                "username": p.get("username", "Listener"),
                "display_name": p.get("display_name", "Listener"),
                "avatar_url": p.get("avatar_url"),
                "is_guest": p.get("is_guest", False),
                "role": m.get("role", "listener"),
                "can_control_playback": m.get("can_control_playback", False),
                "can_manage_queue": m.get("can_manage_queue", False),
                "can_invite": m.get("can_invite", False),
                "can_moderate": m.get("can_moderate", False),
                "joined_at": m.get("joined_at"),
            })

        user_m = next((m for m in members_data if m["user_id"] == user_id), None)
        user_role = "host" if is_host else (user_m.get("role", "listener") if user_m else "guest")
        user_permissions = {
            "is_host": is_host,
            "role": user_role,
            "can_control_playback": is_host or (user_m is not None and (user_m.get("can_control_playback", False) or user_role in ("dj",))),
            "can_manage_queue": is_host or (user_m is not None and (user_m.get("can_manage_queue", False) or user_role in ("dj", "queue_manager"))),
            "can_invite": is_host or (user_m is not None and (user_m.get("can_invite", False) or user_role in ("dj", "queue_manager", "moderator"))),
            "can_moderate": is_host or (user_m is not None and (user_m.get("can_moderate", False) or user_role in ("moderator",))),
        }

        host_prof = room.get("profiles") or {}
        host_name = host_prof.get("display_name") or host_prof.get("username", "Host")

        return {
            **room,
            "host_name": host_name,
            "playback_position_ms": est_position_ms,
            "current_track": current_track,
            "queue_tracks": queue_tracks,
            "normalized_queue": normalized_queue,
            "revision": room.get("revision", 1),
            "playback_rate": float(room.get("playback_rate", 1.0)),
            "server_timestamp": room.get("server_timestamp") or room.get("playback_updated_at"),
            "scheduled_start_ms": room.get("scheduled_start_ms"),
            "members": member_list,
            "listener_count": len(member_list) + 1,
            "user_permissions": user_permissions,
        }

    room = db.rooms.get(room_id)
    if not room or not room.get("is_active"):
        raise HTTPException(status_code=404, detail="Room not found or no longer active")

    is_host = user_id == room.get("host_id")
    members_map = db.room_members.get(room_id, {})
    is_member = user_id in members_map

    if not room.get("is_public") and not (is_host or is_member):
        if not invite_token or invite_token != room.get("invite_token"):
            raise HTTPException(status_code=403, detail="Valid invite link required to view private room")

    normalized_queue = get_room_queue_items(room_id)
    queue_tracks = []
    if normalized_queue:
        for item in normalized_queue:
            t = db.tracks.get(item["track_id"])
            if t:
                queue_tracks.append(t)
    else:
        for tid in room.get("queue", []):
            t = db.tracks.get(tid)
            if t:
                queue_tracks.append(t)

    current_track = db.tracks.get(room.get("current_track_id")) if room.get("current_track_id") else None

    est_position_ms = room.get("playback_position_ms", 0)
    if room.get("is_playing") and room.get("playback_updated_at"):
        try:
            updated_at = datetime.fromisoformat(room["playback_updated_at"])
            delta = (datetime.now(timezone.utc) - updated_at).total_seconds() * 1000
            est_position_ms += int(delta)
        except Exception:
            pass

    member_list = []
    for uid, m in members_map.items():
        p = db.profiles.get(uid, {})
        m_role = m.get("role", "listener")
        member_list.append({
            "user_id": uid,
            "username": p.get("username", "Listener"),
            "display_name": p.get("display_name", "Listener"),
            "avatar_url": p.get("avatar_url"),
            "is_guest": p.get("is_guest", False),
            "role": m_role,
            "can_control_playback": m.get("can_control_playback", False),
            "can_manage_queue": m.get("can_manage_queue", False),
            "can_invite": m.get("can_invite", False),
            "can_moderate": m.get("can_moderate", False),
            "joined_at": m.get("joined_at"),
        })

    user_m = members_map.get(user_id)
    user_role = "host" if is_host else (user_m.get("role", "listener") if user_m else "guest")
    user_permissions = {
        "is_host": is_host,
        "role": user_role,
        "can_control_playback": is_host or (is_member and (user_m.get("can_control_playback", False) or user_role in ("dj",))),
        "can_manage_queue": is_host or (is_member and (user_m.get("can_manage_queue", False) or user_role in ("dj", "queue_manager"))),
        "can_invite": is_host or (is_member and (user_m.get("can_invite", False) or user_role in ("dj", "queue_manager", "moderator"))),
        "can_moderate": is_host or (is_member and (user_m.get("can_moderate", False) or user_role in ("moderator",))),
    }

    return {
        **room,
        "playback_position_ms": est_position_ms,
        "current_track": current_track,
        "queue_tracks": queue_tracks,
        "normalized_queue": normalized_queue,
        "revision": room.get("revision", 1),
        "playback_rate": float(room.get("playback_rate", 1.0)),
        "server_timestamp": room.get("server_timestamp") or room.get("playback_updated_at"),
        "scheduled_start_ms": room.get("scheduled_start_ms"),
        "members": member_list,
        "listener_count": len(member_list) + 1,
        "user_permissions": user_permissions,
    }


@router.post("/{room_id}/join")
async def join_room(
    room_id: str,
    invite_token: Optional[str] = Query(None),
    user: dict = Depends(get_current_user)
):
    """Join room as a listener."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        r_res = admin.table("rooms").select("*").eq("id", room_id).execute()
        if not r_res.data or not r_res.data[0].get("is_active"):
            raise HTTPException(status_code=404, detail="Room not found")
        room = r_res.data[0]
        if user_id == room["host_id"]:
            try:
                admin.table("room_members").upsert({
                    "room_id": room_id,
                    "user_id": user_id,
                    "role": "host",
                    "can_control_playback": True,
                    "can_manage_queue": True,
                    "can_invite": True,
                    "can_moderate": True,
                }, on_conflict="room_id,user_id").execute()
            except Exception:
                pass
            return {"message": "You are the room host"}

        if not room.get("is_public") and invite_token != room.get("invite_token"):
            raise HTTPException(status_code=403, detail="Valid invitation required for private room")

        m_res = admin.table("room_members").select("id").eq("room_id", room_id).execute()
        if len(m_res.data or []) + 1 >= room.get("max_members", 30):
            # Check if user already member
            existing = [m for m in (m_res.data or []) if m.get("user_id") == user_id]
            if not existing:
                raise HTTPException(status_code=400, detail="Room is full")

        admin.table("room_members").upsert({
            "room_id": room_id,
            "user_id": user_id,
            "can_control_playback": False,
            "can_manage_queue": False,
        }, on_conflict="room_id,user_id").execute()
        return {"message": "Joined room successfully"}

    room = db.rooms.get(room_id)
    if not room or not room.get("is_active"):
        raise HTTPException(status_code=404, detail="Room not found")

    if user_id == room["host_id"]:
        return {"message": "You are the room host"}

    if not room.get("is_public") and invite_token != room.get("invite_token"):
        raise HTTPException(status_code=403, detail="Valid invitation required for private room")

    members = db.room_members.setdefault(room_id, {})
    if len(members) + 1 >= room.get("max_members", 30) and user_id not in members:
        raise HTTPException(status_code=400, detail="Room is full")

    if user_id not in members:
        members[user_id] = {
            "can_control_playback": False,
            "can_manage_queue": False,
            "joined_at": datetime.now(timezone.utc).isoformat(),
        }

    return {"message": "Joined room successfully"}


@router.post("/{room_id}/leave")
async def leave_room(room_id: str, user: dict = Depends(get_current_user)):
    """Leave the room."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        r_res = admin.table("rooms").select("*").eq("id", room_id).execute()
        if not r_res.data:
            raise HTTPException(status_code=404, detail="Room not found")
        room = r_res.data[0]

        if user_id == room["host_id"]:
            mem_res = admin.table("room_members").select("*").eq("room_id", room_id).order("joined_at", desc=False).execute()
            if mem_res.data:
                new_host = mem_res.data[0]
                new_host_id = new_host["user_id"]
                admin.table("rooms").update({"host_id": new_host_id}).eq("id", room_id).execute()
                admin.table("room_members").delete().eq("room_id", room_id).eq("user_id", new_host_id).execute()
            else:
                admin.table("rooms").update({"is_active": False}).eq("id", room_id).execute()
                return {"message": "Host left; room closed"}
        else:
            admin.table("room_members").delete().eq("room_id", room_id).eq("user_id", user_id).execute()

        return {"message": "Left room successfully"}

    room = db.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    if user_id == room["host_id"]:
        members = db.room_members.get(room_id, {})
        if members:
            new_host_id = next(iter(members.keys()))
            new_host_profile = db.profiles.get(new_host_id, {})
            room["host_id"] = new_host_id
            room["host_name"] = new_host_profile.get("display_name", "Host")
            del members[new_host_id]
        else:
            room["is_active"] = False
            return {"message": "Host left; room closed"}

    members = db.room_members.get(room_id, {})
    if user_id in members:
        del members[user_id]

    return {"message": "Left room successfully"}


@router.patch("/{room_id}/playback")
async def update_playback(room_id: str, req: PlaybackSyncRequest, user: dict = Depends(get_current_user)):
    """Update room playback state (play, pause, seek, track change). Requires permission."""
    user_id = user["sub"]
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        r_res = admin.table("rooms").select("*").eq("id", room_id).execute()
        if not r_res.data or not r_res.data[0].get("is_active"):
            raise HTTPException(status_code=404, detail="Room not found")
        room = r_res.data[0]

        is_host = user_id == room["host_id"]
        mem_res = admin.table("room_members").select("*").eq("room_id", room_id).eq("user_id", user_id).execute()
        mem_data = mem_res.data[0] if mem_res.data else {}
        can_control = is_host or mem_data.get("can_control_playback", False) or mem_data.get("role") in ("host", "dj")

        if not can_control:
            raise HTTPException(status_code=403, detail="Playback control permission required")

        new_rev = room.get("revision", 1) + 1
        up_data = {
            "playback_position_ms": req.playback_position_ms,
            "is_playing": req.is_playing,
            "playback_updated_at": now_iso,
            "revision": new_rev,
            "server_timestamp": now_iso,
            "updated_by": user_id,
        }
        if req.current_track_id:
            up_data["current_track_id"] = req.current_track_id
            # Track access grant if private
            t_res = admin.table("tracks").select("is_private, uploaded_by").eq("id", req.current_track_id).execute()
            if t_res.data and t_res.data[0].get("is_private"):
                admin.table("track_access").insert({
                    "track_id": req.current_track_id,
                    "owner_id": t_res.data[0]["uploaded_by"],
                    "room_id": room_id,
                }).execute()

        try:
            res = admin.table("rooms").update(up_data).eq("id", room_id).execute()
        except Exception as exc:
            if "PGRST204" in str(exc) or "column" in str(exc).lower():
                legacy_up = {k: v for k, v in up_data.items() if k in {"playback_position_ms", "is_playing", "playback_updated_at", "current_track_id"}}
                res = admin.table("rooms").update(legacy_up).eq("id", room_id).execute()
            else:
                raise
        out_room = res.data[0] if res.data else {**room, **up_data}
        broadcast_room_event(room_id, "playback_sync", {"action": "UPDATE_PLAYBACK", "room": out_room, "revision": new_rev})
        return {"message": "Playback state updated", "room": out_room}

    room = db.rooms.get(room_id)
    if not room or not room.get("is_active"):
        raise HTTPException(status_code=404, detail="Room not found")

    is_host = user_id == room["host_id"]
    mem_info = db.room_members.get(room_id, {}).get(user_id, {})
    can_control = is_host or mem_info.get("can_control_playback", False) or mem_info.get("role") in ("host", "dj")

    if not can_control:
        raise HTTPException(status_code=403, detail="Playback control permission required")

    if req.current_track_id:
        track = db.tracks.get(req.current_track_id)
        if track:
            room["current_track_id"] = req.current_track_id
            if track.get("is_private"):
                db.track_access.append({
                    "track_id": req.current_track_id,
                    "owner_id": track.get("uploaded_by"),
                    "room_id": room_id,
                    "granted_at": now_iso
                })

    new_rev = room.get("revision", 1) + 1
    room["playback_position_ms"] = req.playback_position_ms
    room["is_playing"] = req.is_playing
    room["playback_updated_at"] = now_iso
    room["revision"] = new_rev
    room["server_timestamp"] = now_iso
    room["updated_by"] = user_id
    room["updated_at"] = now_iso

    broadcast_room_event(room_id, "playback_sync", {"action": "UPDATE_PLAYBACK", "room": room, "revision": new_rev})
    return {"message": "Playback state updated", "room": room}


@router.post("/{room_id}/queue")
async def update_queue(room_id: str, req: QueueUpdateRequest, user: dict = Depends(get_current_user)):
    """Update room queue. Requires queue management permission."""
    user_id = user["sub"]
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        r_res = admin.table("rooms").select("*").eq("id", room_id).execute()
        if not r_res.data or not r_res.data[0].get("is_active"):
            raise HTTPException(status_code=404, detail="Room not found")
        room = r_res.data[0]

        is_host = user_id == room["host_id"]
        mem_res = admin.table("room_members").select("*").eq("room_id", room_id).eq("user_id", user_id).execute()
        mem_data = mem_res.data[0] if mem_res.data else {}
        can_manage = is_host or mem_data.get("can_manage_queue", False) or mem_data.get("role") in ("host", "moderator", "dj", "queue_manager")

        if not can_manage:
            raise HTTPException(status_code=403, detail="Queue management permission required")

        new_rev = room.get("revision", 1) + 1
        try:
            admin.table("rooms").update({
                "queue": req.queue,
                "revision": new_rev,
                "server_timestamp": now_iso,
                "updated_by": user_id,
                "updated_at": now_iso,
            }).eq("id", room_id).execute()
        except Exception as exc:
            if "PGRST204" in str(exc) or "column" in str(exc).lower():
                admin.table("rooms").update({"queue": req.queue}).eq("id", room_id).execute()
            else:
                raise

        # Sync normalized room_queue table non-destructively (preserves votes & IDs)
        try:
            existing_res = admin.table("room_queue").select("*").eq("room_id", room_id).is_("played_at", "null").order("position").execute()
            existing_items = existing_res.data or []
            remaining_existing = list(existing_items)

            for idx, tid in enumerate(req.queue):
                match = next((item for item in remaining_existing if item.get("track_id") == tid), None)
                if match:
                    remaining_existing.remove(match)
                    if match.get("position") != idx:
                        admin.table("room_queue").update({"position": idx}).eq("id", match["id"]).execute()
                else:
                    admin.table("room_queue").insert({
                        "room_id": room_id,
                        "track_id": tid,
                        "added_by": user_id,
                        "position": idx,
                        "vote_count": 0,
                    }).execute()

            for removed_item in remaining_existing:
                admin.table("room_queue").delete().eq("id", removed_item["id"]).execute()
        except Exception as exc:
            err_code = getattr(exc, "code", None)
            if err_code is None and hasattr(exc, "info") and isinstance(exc.info, dict):
                err_code = exc.info.get("code")
            if err_code is None and len(getattr(exc, "args", ())) > 0 and isinstance(exc.args[0], dict):
                err_code = exc.args[0].get("code")
            err_str = str(exc)

            is_missing_table = (
                err_code in ("PGRST204", "PGRST200", "42P01") or
                ("room_queue" in err_str.lower() and any(k in err_str.lower() for k in ("does not exist", "schema cache", "not find", "column", "table")))
            )
            if is_missing_table:
                logger.info("Normalized room_queue table unmigrated; skipped dual-write reconciliation for room %s.", room_id)
            else:
                logger.error("Database failure while reconciling normalized room_queue for room %s: %s", room_id, err_str)
                raise HTTPException(status_code=500, detail="Database failure reconciling queue")

        # Authorize private tracks in queue
        if req.queue:
            t_res = admin.table("tracks").select("id, is_private, uploaded_by").in_("id", req.queue).execute()
            for t in (t_res.data or []):
                if t.get("is_private"):
                    admin.table("track_access").insert({
                        "track_id": t["id"],
                        "owner_id": t["uploaded_by"],
                        "room_id": room_id,
                    }).execute()

        broadcast_room_event(room_id, "queue_updated", {"action": "UPDATE_QUEUE", "queue": req.queue, "revision": new_rev})
        return {"message": "Queue updated", "queue": req.queue}

    room = db.rooms.get(room_id)
    if not room or not room.get("is_active"):
        raise HTTPException(status_code=404, detail="Room not found")

    is_host = user_id == room["host_id"]
    mem_info = db.room_members.get(room_id, {}).get(user_id, {})
    can_manage = is_host or mem_info.get("can_manage_queue", False) or mem_info.get("role") in ("host", "moderator", "dj", "queue_manager")

    if not can_manage:
        raise HTTPException(status_code=403, detail="Queue management permission required")

    new_rev = room.get("revision", 1) + 1
    room["queue"] = req.queue
    room["revision"] = new_rev
    room["server_timestamp"] = now_iso
    room["updated_by"] = user_id
    room["updated_at"] = now_iso

    # Dual-write sync to normalized room_queues in-memory non-destructively
    existing_items = db.room_queues.get(room_id, [])
    unplayed = [i for i in existing_items if i.get("played_at") is None]
    played = [i for i in existing_items if i.get("played_at") is not None]

    remaining_unplayed = list(unplayed)
    new_unplayed = []
    for idx, tid in enumerate(req.queue):
        match = next((i for i in remaining_unplayed if i.get("track_id") == tid), None)
        if match:
            remaining_unplayed.remove(match)
            match["position"] = idx
            new_unplayed.append(match)
        else:
            new_unplayed.append({
                "id": str(uuid.uuid4()),
                "room_id": room_id,
                "track_id": tid,
                "added_by": user_id,
                "position": idx,
                "vote_count": 0,
                "played_at": None,
                "created_at": now_iso,
            })

    for rem in remaining_unplayed:
        db.room_queue_votes.pop(rem["id"], None)

    db.room_queues[room_id] = played + new_unplayed

    for tid in req.queue:
        t = db.tracks.get(tid)
        if t and t.get("is_private"):
            db.track_access.append({
                "track_id": tid,
                "owner_id": t.get("uploaded_by"),
                "room_id": room_id,
                "granted_at": now_iso
            })

    broadcast_room_event(room_id, "queue_updated", {"action": "UPDATE_QUEUE", "queue": room["queue"], "revision": new_rev})
    return {"message": "Queue updated", "queue": room["queue"]}


@router.patch("/{room_id}/members/{target_user_id}/permissions")
async def update_member_permissions(
    room_id: str,
    target_user_id: str,
    req: PermissionsUpdateRequest,
    user: dict = Depends(get_current_user)
):
    """Host or moderator delegates or revokes member permissions."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        r_res = admin.table("rooms").select("host_id").eq("id", room_id).execute()
        if not r_res.data:
            raise HTTPException(status_code=404, detail="Room not found")
        room = r_res.data[0]

        if target_user_id == room["host_id"]:
            raise HTTPException(status_code=400, detail="Cannot modify host permissions")

        if req.role == "host":
            raise HTTPException(status_code=403, detail="Host role can only be assigned via host transfer")

        is_host = user_id == room["host_id"]
        caller_res = admin.table("room_members").select("*").eq("room_id", room_id).eq("user_id", user_id).execute()
        caller_mem = caller_res.data[0] if caller_res.data else {}
        is_mod = is_host or caller_mem.get("can_moderate", False) or caller_mem.get("role") in ("host", "moderator")

        if not is_mod:
            raise HTTPException(status_code=403, detail="Only host or moderator can update permissions")

        if not is_host:
            if req.can_moderate is not None or req.role == "moderator":
                raise HTTPException(status_code=403, detail="Only host can grant or modify moderator privileges")

        target_res = admin.table("room_members").select("*").eq("room_id", room_id).eq("user_id", target_user_id).execute()
        if not target_res.data:
            raise HTTPException(status_code=404, detail="Member not in room")
        target_mem = target_res.data[0]

        if not is_host and (target_mem.get("role") == "moderator" or target_mem.get("can_moderate", False)):
            raise HTTPException(status_code=403, detail="Moderators cannot modify other moderators")

        up_data = {}
        if req.can_control_playback is not None:
            up_data["can_control_playback"] = req.can_control_playback
        if req.can_manage_queue is not None:
            up_data["can_manage_queue"] = req.can_manage_queue
        if req.can_invite is not None:
            up_data["can_invite"] = req.can_invite
        if req.can_moderate is not None:
            up_data["can_moderate"] = req.can_moderate
        if req.role is not None:
            up_data["role"] = req.role

        try:
            admin.table("room_members").update(up_data).eq("room_id", room_id).eq("user_id", target_user_id).execute()
        except Exception as exc:
            if "PGRST204" in str(exc) or "column" in str(exc).lower():
                legacy_up = {k: v for k, v in up_data.items() if k in {"can_control_playback", "can_manage_queue"}}
                admin.table("room_members").update(legacy_up).eq("room_id", room_id).eq("user_id", target_user_id).execute()
            else:
                raise
        broadcast_room_event(room_id, "permissions_updated", {"target_user_id": target_user_id, "permissions": up_data})
        return {"message": "Permissions updated successfully"}

    room = db.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    if target_user_id == room["host_id"]:
        raise HTTPException(status_code=400, detail="Cannot modify host permissions")

    if req.role == "host":
        raise HTTPException(status_code=403, detail="Host role can only be assigned via host transfer")

    members = db.room_members.get(room_id, {})
    is_host = user["sub"] == room["host_id"]
    caller_mem = members.get(user_id, {})
    is_mod = is_host or caller_mem.get("can_moderate", False) or caller_mem.get("role") in ("host", "moderator")

    if not is_mod:
        raise HTTPException(status_code=403, detail="Only host or moderator can update permissions")

    if not is_host:
        if req.can_moderate is not None or req.role == "moderator":
            raise HTTPException(status_code=403, detail="Only host can grant or modify moderator privileges")

    if target_user_id not in members:
        raise HTTPException(status_code=404, detail="Member not in room")

    target = members[target_user_id]
    if not is_host and (target.get("role") == "moderator" or target.get("can_moderate", False)):
        raise HTTPException(status_code=403, detail="Moderators cannot modify other moderators")

    if req.can_control_playback is not None:
        target["can_control_playback"] = req.can_control_playback
    if req.can_manage_queue is not None:
        target["can_manage_queue"] = req.can_manage_queue
    if req.can_invite is not None:
        target["can_invite"] = req.can_invite
    if req.can_moderate is not None:
        target["can_moderate"] = req.can_moderate
    if req.role is not None:
        target["role"] = req.role

    broadcast_room_event(room_id, "permissions_updated", {"target_user_id": target_user_id, "permissions": req.model_dump(exclude_unset=True)})
    return {"message": "Permissions updated successfully"}


@router.delete("/{room_id}/members/{target_user_id}")
async def kick_member(
    room_id: str,
    target_user_id: str,
    user: dict = Depends(get_current_user)
):
    """Host or moderator kicks a member from the room."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        r_res = admin.table("rooms").select("host_id").eq("id", room_id).execute()
        if not r_res.data:
            raise HTTPException(status_code=404, detail="Room not found")
        room = r_res.data[0]

        if target_user_id == room["host_id"]:
            raise HTTPException(status_code=400, detail="Cannot kick the room host")

        is_host = user_id == room["host_id"]
        caller_res = admin.table("room_members").select("*").eq("room_id", room_id).eq("user_id", user_id).execute()
        caller_mem = caller_res.data[0] if caller_res.data else {}
        is_mod = is_host or caller_mem.get("can_moderate", False) or caller_mem.get("role") in ("host", "moderator")

        if not is_mod:
            raise HTTPException(status_code=403, detail="Only the room host or moderator can kick members")

        target_res = admin.table("room_members").select("*").eq("room_id", room_id).eq("user_id", target_user_id).execute()
        if not target_res.data:
            raise HTTPException(status_code=404, detail="Member not in room")
        target_mem = target_res.data[0]

        if not is_host and (target_mem.get("role") == "moderator" or target_mem.get("can_moderate", False)):
            raise HTTPException(status_code=403, detail="Moderators cannot kick other moderators")

        admin.table("room_members").delete().eq("room_id", room_id).eq("user_id", target_user_id).execute()
        broadcast_room_event(room_id, "member_kicked", {"target_user_id": target_user_id})
        return {"message": "Member removed from room"}

    room = db.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    if target_user_id == room["host_id"]:
        raise HTTPException(status_code=400, detail="Cannot kick the room host")

    members = db.room_members.get(room_id, {})
    is_host = user["sub"] == room["host_id"]
    caller_mem = members.get(user_id, {})
    is_mod = is_host or caller_mem.get("can_moderate", False) or caller_mem.get("role") in ("host", "moderator")

    if not is_mod:
        raise HTTPException(status_code=403, detail="Only the room host or moderator can kick members")

    if target_user_id not in members:
        raise HTTPException(status_code=404, detail="Member not in room")

    target = members[target_user_id]
    if not is_host and (target.get("role") == "moderator" or target.get("can_moderate", False)):
        raise HTTPException(status_code=403, detail="Moderators cannot kick other moderators")

    del members[target_user_id]
    broadcast_room_event(room_id, "member_kicked", {"target_user_id": target_user_id})
    return {"message": "Member removed from room"}


@router.post("/{room_id}/transfer")
async def transfer_host(
    room_id: str,
    req: TransferHostRequest,
    user: dict = Depends(get_current_user)
):
    """Host transfers ownership to another member."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        r_res = admin.table("rooms").select("host_id").eq("id", room_id).execute()
        if not r_res.data:
            raise HTTPException(status_code=404, detail="Room not found")
        if user_id != r_res.data[0]["host_id"]:
            raise HTTPException(status_code=403, detail="Only the host can transfer room ownership")

        mem_res = admin.table("room_members").select("id").eq("room_id", room_id).eq("user_id", req.new_host_id).execute()
        if not mem_res.data:
            raise HTTPException(status_code=400, detail="New host must be an active room member")

        admin.table("rooms").update({"host_id": req.new_host_id}).eq("id", room_id).execute()
        admin.table("room_members").delete().eq("room_id", room_id).eq("user_id", req.new_host_id).execute()
        admin.table("room_members").insert({"room_id": room_id, "user_id": user_id}).execute()

        broadcast_room_event(room_id, "host_transferred", {"new_host_id": req.new_host_id})
        return {"message": "Host transferred successfully", "new_host_id": req.new_host_id}

    room = db.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    if user["sub"] != room["host_id"]:
        raise HTTPException(status_code=403, detail="Only the host can transfer room ownership")

    members = db.room_members.get(room_id, {})
    if req.new_host_id not in members:
        raise HTTPException(status_code=400, detail="New host must be an active room member")

    old_host_id = room["host_id"]
    new_host_profile = db.profiles.get(req.new_host_id, {})

    room["host_id"] = req.new_host_id
    room["host_name"] = new_host_profile.get("display_name", "Host")
    room["updated_at"] = datetime.now(timezone.utc).isoformat()

    del members[req.new_host_id]
    members[old_host_id] = {
        "can_control_playback": False,
        "can_manage_queue": False,
        "joined_at": datetime.now(timezone.utc).isoformat(),
    }

    broadcast_room_event(room_id, "host_transferred", {"new_host_id": req.new_host_id})
    return {"message": "Host transferred successfully", "new_host_id": req.new_host_id}


@router.delete("/{room_id}")
async def close_room(room_id: str, user: dict = Depends(get_current_user)):
    """Host closes the room."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        r_res = admin.table("rooms").select("host_id").eq("id", room_id).execute()
        if not r_res.data:
            raise HTTPException(status_code=404, detail="Room not found")
        if user_id != r_res.data[0]["host_id"]:
            raise HTTPException(status_code=403, detail="Only host can close the room")

        admin.table("rooms").update({"is_active": False}).eq("id", room_id).execute()
        broadcast_room_event(room_id, "room_closed", {"room_id": room_id})
        return {"message": "Room closed"}

    room = db.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    if user["sub"] != room["host_id"]:
        raise HTTPException(status_code=403, detail="Only host can close the room")

    room["is_active"] = False
    broadcast_room_event(room_id, "room_closed", {"room_id": room_id})
    return {"message": "Room closed"}


@router.get("/{room_id}/messages")
async def get_room_messages(room_id: str, user: Optional[dict] = Depends(get_optional_user)):
    """Get chat messages for room."""
    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        res = admin.table("room_messages").select(
            "id, room_id, user_id, content, created_at, profiles(username, display_name, avatar_url)"
        ).eq("room_id", room_id).order("created_at", desc=False).limit(50).execute()

        formatted = []
        for row in (res.data or []):
            prof = row.get("profiles") or {}
            formatted.append({
                "id": row["id"],
                "room_id": row["room_id"],
                "user_id": row["user_id"],
                "username": prof.get("username", "Listener"),
                "display_name": prof.get("display_name", "Listener"),
                "avatar_url": prof.get("avatar_url"),
                "content": row["content"],
                "created_at": row["created_at"],
            })
        return formatted

    messages = db.room_messages.get(room_id, [])
    return messages[-50:]


@router.post("/{room_id}/messages")
async def send_room_message(
    room_id: str,
    req: ChatMessageRequest,
    user: dict = Depends(get_current_user)
):
    """Send text message to room chat."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        r_res = admin.table("rooms").select("is_active").eq("id", room_id).execute()
        if not r_res.data or not r_res.data[0].get("is_active"):
            raise HTTPException(status_code=404, detail="Room not found")

        ins_res = admin.table("room_messages").insert({
            "room_id": room_id,
            "user_id": user_id,
            "content": req.content.strip(),
        }).execute()

        msg_row = ins_res.data[0] if ins_res.data else {}
        prof_res = admin.table("profiles").select("username, display_name, avatar_url").eq("id", user_id).execute()
        prof = prof_res.data[0] if prof_res.data else {}

        return {
            "id": msg_row.get("id", str(uuid.uuid4())),
            "room_id": room_id,
            "user_id": user_id,
            "username": prof.get("username", user.get("username", "Listener")),
            "display_name": prof.get("display_name", "Listener"),
            "avatar_url": prof.get("avatar_url"),
            "content": req.content.strip(),
            "created_at": msg_row.get("created_at", datetime.now(timezone.utc).isoformat()),
        }

    room = db.rooms.get(room_id)
    if not room or not room.get("is_active"):
        raise HTTPException(status_code=404, detail="Room not found")

    profile = db.profiles.get(user_id, {})
    msg = {
        "id": str(uuid.uuid4()),
        "room_id": room_id,
        "user_id": user_id,
        "username": profile.get("username", user.get("username", "Listener")),
        "display_name": profile.get("display_name", "Listener"),
        "avatar_url": profile.get("avatar_url"),
        "content": req.content.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    db.room_messages.setdefault(room_id, []).append(msg)
    return msg
