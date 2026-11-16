"""Favorites management endpoints."""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from app.auth import get_current_user
from app.db import db

router = APIRouter(prefix="/api/favorites", tags=["favorites"])


@router.get("")
async def list_favorites(user: dict = Depends(get_current_user)):
    """List current user's favorite tracks."""
    user_id = user["sub"]
    fav_ids = db.favorites.get(user_id, set())

    results = []
    for tid in fav_ids:
        t = db.tracks.get(tid)
        if t:
            results.append({**t, "is_favorite": True})
    return results


@router.post("/{track_id}")
async def add_favorite(track_id: str, user: dict = Depends(get_current_user)):
    """Add track to favorites."""
    if user.get("is_guest"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Guest users cannot permanently favorite tracks. Please create an account."
        )

    user_id = user["sub"]
    track = db.tracks.get(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    user_favs = db.favorites.setdefault(user_id, set())
    user_favs.add(track_id)
    return {"message": "Track added to favorites", "track_id": track_id}


@router.delete("/{track_id}")
async def remove_favorite(track_id: str, user: dict = Depends(get_current_user)):
    """Remove track from favorites."""
    user_id = user["sub"]
    user_favs = db.favorites.get(user_id, set())
    user_favs.discard(track_id)
    return {"message": "Track removed from favorites", "track_id": track_id}
