"""Listening Rooms and playback synchronization endpoints."""

import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth import get_current_user, get_optional_user
from app.db import db

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
    can_control_playback: bool
    can_manage_queue: bool


class TransferHostRequest(BaseModel):
    new_host_id: str


class ChatMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)


@router.get("")
async def list_global_rooms(
    q: Optional[str] = Query(None, description="Search rooms by name or host")
):
    """List live public rooms (Global Rooms)."""
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
        # Host is also a listener
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
    profile = db.profiles.get(user_id, {})
    host_name = profile.get("display_name") or user.get("username", "Host")

    room_id = str(uuid.uuid4())
    invite_token = secrets.token_urlsafe(16)

    # Initial track from public library if available
    first_track_id = next(iter(db.tracks.keys()), None)

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
        "playback_updated_at": datetime.now(timezone.utc).isoformat(),
        "queue": [first_track_id] if first_track_id else [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    db.rooms[room_id] = room
    db.room_members[room_id] = {}
    db.room_messages[room_id] = []

    return room


@router.get("/{room_id}")
async def get_room(
    room_id: str,
    invite_token: Optional[str] = Query(None),
    user: Optional[dict] = Depends(get_optional_user)
):
    """Get full room details, current track, queue, and member list."""
    room = db.rooms.get(room_id)
    if not room or not room.get("is_active"):
        raise HTTPException(status_code=404, detail="Room not found or no longer active")

    user_id = user.get("sub") if user else None
    is_host = user_id == room.get("host_id")
    members_map = db.room_members.get(room_id, {})
    is_member = user_id in members_map

    # Private room access check
    if not room.get("is_public") and not (is_host or is_member):
        if not invite_token or invite_token != room.get("invite_token"):
            raise HTTPException(status_code=403, detail="Valid invite link required to view private room")

    # Resolve track objects in queue
    queue_tracks = []
    for tid in room.get("queue", []):
        t = db.tracks.get(tid)
        if t:
            queue_tracks.append(t)

    current_track = db.tracks.get(room.get("current_track_id")) if room.get("current_track_id") else None

    # Calculate real-time estimated playback position
    est_position_ms = room.get("playback_position_ms", 0)
    if room.get("is_playing") and room.get("playback_updated_at"):
        try:
            updated_at = datetime.fromisoformat(room["playback_updated_at"])
            delta = (datetime.now(timezone.utc) - updated_at).total_seconds() * 1000
            est_position_ms += int(delta)
        except Exception:
            pass

    # Build member list
    member_list = []
    for uid, m in members_map.items():
        p = db.profiles.get(uid, {})
        member_list.append({
            "user_id": uid,
            "username": p.get("username", "Listener"),
            "display_name": p.get("display_name", "Listener"),
            "avatar_url": p.get("avatar_url"),
            "is_guest": p.get("is_guest", False),
            "can_control_playback": m.get("can_control_playback", False),
            "can_manage_queue": m.get("can_manage_queue", False),
            "joined_at": m.get("joined_at"),
        })

    user_permissions = {
        "is_host": is_host,
        "can_control_playback": is_host or (is_member and members_map.get(user_id, {}).get("can_control_playback", False)),
        "can_manage_queue": is_host or (is_member and members_map.get(user_id, {}).get("can_manage_queue", False)),
    }

    return {
        **room,
        "playback_position_ms": est_position_ms,
        "current_track": current_track,
        "queue_tracks": queue_tracks,
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
    room = db.rooms.get(room_id)
    if not room or not room.get("is_active"):
        raise HTTPException(status_code=404, detail="Room not found")

    user_id = user["sub"]
    if user_id == room["host_id"]:
        return {"message": "You are the room host"}

    # Private room check
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
    room = db.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    user_id = user["sub"]
    if user_id == room["host_id"]:
        # If host leaves without transfer, automatically close room or transfer to oldest member
        members = db.room_members.get(room_id, {})
        if members:
            # Transfer to first member
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
    room = db.rooms.get(room_id)
    if not room or not room.get("is_active"):
        raise HTTPException(status_code=404, detail="Room not found")

    user_id = user["sub"]
    is_host = user_id == room["host_id"]
    can_control = is_host or db.room_members.get(room_id, {}).get(user_id, {}).get("can_control_playback", False)

    if not can_control:
        raise HTTPException(status_code=403, detail="Playback control permission required")

    if req.current_track_id:
        track = db.tracks.get(req.current_track_id)
        if track:
            room["current_track_id"] = req.current_track_id
            # Grant room access if it's a private personal upload
            if track.get("is_private"):
                db.track_access.append({
                    "track_id": req.current_track_id,
                    "owner_id": track.get("uploaded_by"),
                    "room_id": room_id,
                    "granted_at": datetime.now(timezone.utc).isoformat()
                })

    room["playback_position_ms"] = req.playback_position_ms
    room["is_playing"] = req.is_playing
    room["playback_updated_at"] = datetime.now(timezone.utc).isoformat()
    room["updated_at"] = datetime.now(timezone.utc).isoformat()

    return {"message": "Playback state updated", "room": room}


@router.post("/{room_id}/queue")
async def update_queue(room_id: str, req: QueueUpdateRequest, user: dict = Depends(get_current_user)):
    """Update room queue. Requires queue management permission."""
    room = db.rooms.get(room_id)
    if not room or not room.get("is_active"):
        raise HTTPException(status_code=404, detail="Room not found")

    user_id = user["sub"]
    is_host = user_id == room["host_id"]
    can_manage = is_host or db.room_members.get(room_id, {}).get(user_id, {}).get("can_manage_queue", False)

    if not can_manage:
        raise HTTPException(status_code=403, detail="Queue management permission required")

    room["queue"] = req.queue
    room["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Authorize any private tracks in queue for this room
    for tid in req.queue:
        t = db.tracks.get(tid)
        if t and t.get("is_private"):
            db.track_access.append({
                "track_id": tid,
                "owner_id": t.get("uploaded_by"),
                "room_id": room_id,
                "granted_at": datetime.now(timezone.utc).isoformat()
            })

    return {"message": "Queue updated", "queue": room["queue"]}


@router.patch("/{room_id}/members/{target_user_id}/permissions")
async def update_member_permissions(
    room_id: str,
    target_user_id: str,
    req: PermissionsUpdateRequest,
    user: dict = Depends(get_current_user)
):
    """Host delegates or revokes member permissions."""
    room = db.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    if user["sub"] != room["host_id"]:
        raise HTTPException(status_code=403, detail="Only the room host can update permissions")

    members = db.room_members.get(room_id, {})
    if target_user_id not in members:
        raise HTTPException(status_code=404, detail="Member not in room")

    members[target_user_id]["can_control_playback"] = req.can_control_playback
    members[target_user_id]["can_manage_queue"] = req.can_manage_queue

    return {"message": "Permissions updated successfully"}


@router.delete("/{room_id}/members/{target_user_id}")
async def kick_member(
    room_id: str,
    target_user_id: str,
    user: dict = Depends(get_current_user)
):
    """Host kicks a member from the room."""
    room = db.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    if user["sub"] != room["host_id"]:
        raise HTTPException(status_code=403, detail="Only the room host can kick members")

    members = db.room_members.get(room_id, {})
    if target_user_id in members:
        del members[target_user_id]

    return {"message": "Member removed from room"}


@router.post("/{room_id}/transfer")
async def transfer_host(
    room_id: str,
    req: TransferHostRequest,
    user: dict = Depends(get_current_user)
):
    """Host transfers ownership to another member."""
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

    # Move new host out of members, add old host to members
    del members[req.new_host_id]
    members[old_host_id] = {
        "can_control_playback": False,
        "can_manage_queue": False,
        "joined_at": datetime.now(timezone.utc).isoformat(),
    }

    return {"message": "Host transferred successfully", "new_host_id": req.new_host_id}


@router.delete("/{room_id}")
async def close_room(room_id: str, user: dict = Depends(get_current_user)):
    """Host closes the room."""
    room = db.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    if user["sub"] != room["host_id"]:
        raise HTTPException(status_code=403, detail="Only host can close the room")

    room["is_active"] = False
    return {"message": "Room closed"}


@router.get("/{room_id}/messages")
async def get_room_messages(room_id: str, user: Optional[dict] = Depends(get_optional_user)):
    """Get chat messages for room."""
    messages = db.room_messages.get(room_id, [])
    return messages[-50:]  # last 50 messages


@router.post("/{room_id}/messages")
async def send_room_message(
    room_id: str,
    req: ChatMessageRequest,
    user: dict = Depends(get_current_user)
):
    """Send text message to room chat."""
    room = db.rooms.get(room_id)
    if not room or not room.get("is_active"):
        raise HTTPException(status_code=404, detail="Room not found")

    user_id = user["sub"]
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
