"""Listening history endpoints."""

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.db import db
from app.routers.tracks import format_track
from app.services.supabase_client import supabase_service

router = APIRouter(prefix="/api/history", tags=["history"])


class PlayHistoryRecord(BaseModel):
    track_id: str
    duration_listened_ms: int = Field(0, ge=0)


@router.get("")
async def get_history(
    limit: int = Query(50, ge=1, le=100),
    user: dict = Depends(get_current_user)
):
    """Get recent listening history for current user."""
    user_id = user["sub"]

    if supabase_service.is_live and not user.get("is_guest"):
        admin = supabase_service.get_admin()
        res = admin.table("listening_history").select(
            "id, played_at, duration_listened_ms, tracks(*, artists(id, name), albums(id, title, artwork_url, artwork_thumb_url), track_artwork(original_url, thumb_url))"
        ).eq("user_id", user_id).order("played_at", desc=True).limit(limit).execute()

        fav_res = admin.table("favorites").select("track_id").eq("user_id", user_id).execute()
        user_favs = {row["track_id"] for row in (fav_res.data or [])}

        results = []
        for row in (res.data or []):
            raw_track = row.get("tracks")
            if raw_track:
                results.append({
                    "history_id": row["id"],
                    "played_at": row["played_at"],
                    "duration_listened_ms": row.get("duration_listened_ms", 0),
                    "track": format_track(raw_track, is_favorite=raw_track["id"] in user_favs)
                })
        return results

    user_entries = [
        entry for entry in reversed(db.listening_history)
        if entry.get("user_id") == user_id
    ][:limit]

    results = []
    user_favs = db.favorites.get(user_id, set())

    for entry in user_entries:
        tid = entry.get("track_id")
        t = db.tracks.get(tid)
        if t:
            results.append({
                "history_id": entry.get("id"),
                "played_at": entry.get("played_at"),
                "duration_listened_ms": entry.get("duration_listened_ms"),
                "track": {**t, "is_favorite": tid in user_favs}
            })

    return results


@router.post("")
async def record_play(req: PlayHistoryRecord, user: dict = Depends(get_current_user)):
    """Record a song play event in listening history."""
    user_id = user["sub"]

    if user.get("is_guest"):
        # Guest listening history is retained in memory only
        import uuid
        entry = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "track_id": req.track_id,
            "duration_listened_ms": req.duration_listened_ms,
            "played_at": datetime.now(timezone.utc).isoformat(),
        }
        db.listening_history.append(entry)
        return {"message": "Play history recorded (guest session)"}

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        admin.table("listening_history").insert({
            "user_id": user_id,
            "track_id": req.track_id,
            "duration_listened_ms": req.duration_listened_ms,
        }).execute()
    else:
        track = db.tracks.get(req.track_id)
        if not track:
            raise HTTPException(status_code=404, detail="Track not found")

    import uuid
    entry = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "track_id": req.track_id,
        "duration_listened_ms": req.duration_listened_ms,
        "played_at": datetime.now(timezone.utc).isoformat(),
    }
    db.listening_history.append(entry)
    return {"message": "Play history recorded"}
