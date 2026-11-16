"""Listening history endpoints."""

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.db import db

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
