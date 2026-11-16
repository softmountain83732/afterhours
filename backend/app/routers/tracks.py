"""Tracks and music discovery endpoints."""

from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import get_current_user, get_optional_user
from app.db import db

router = APIRouter(prefix="/api/tracks", tags=["tracks"])


@router.get("")
async def list_tracks(
    q: Optional[str] = Query(None, description="Search query for title, artist, or album"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: Optional[dict] = Depends(get_optional_user)
):
    """
    List and search available tracks.
    Returns public tracks and the user's own uploads.
    """
    user_id = user.get("sub") if user else None

    # Filter tracks: public tracks OR user's own uploads
    accessible = []
    for t in db.tracks.values():
        if not t.get("is_private") or (user_id and t.get("uploaded_by") == user_id):
            accessible.append(t)

    # Filter by search query if provided
    if q and q.strip():
        term = q.strip().lower()
        filtered = []
        for t in accessible:
            title = t.get("title", "").lower()
            artist = t.get("artist_name", "").lower()
            album = t.get("album_title", "").lower()
            if term in title or term in artist or term in album:
                filtered.append(t)
        accessible = filtered

    total = len(accessible)
    paged = accessible[offset: offset + limit]

    # Annotate if favorite for this user
    user_favs = db.favorites.get(user_id, set()) if user_id else set()
    result = []
    for t in paged:
        result.append({
            **t,
            "is_favorite": t["id"] in user_favs
        })

    return {
        "items": result,
        "total": total,
        "limit": limit,
        "offset": offset
    }


@router.get("/my-uploads")
async def get_my_uploads(user: dict = Depends(get_current_user)):
    """Get all personal music uploads by current user."""
    user_id = user["sub"]
    my_tracks = [t for t in db.tracks.values() if t.get("uploaded_by") == user_id]

    user_favs = db.favorites.get(user_id, set())
    return [
        {**t, "is_favorite": t["id"] in user_favs}
        for t in my_tracks
    ]


@router.get("/{track_id}")
async def get_track(track_id: str, user: Optional[dict] = Depends(get_optional_user)):
    """Get single track metadata."""
    track = db.tracks.get(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    user_id = user.get("sub") if user else None

    # Access check: public track or user's own upload or room grant
    if track.get("is_private") and track.get("uploaded_by") != user_id:
        # Check if room grant exists
        has_grant = any(
            g.get("track_id") == track_id and (user_id is not None)
            for g in db.track_access
        )
        if not has_grant:
            raise HTTPException(status_code=403, detail="Access denied to private track")

    user_favs = db.favorites.get(user_id, set()) if user_id else set()
    return {
        **track,
        "is_favorite": track_id in user_favs
    }


@router.delete("/{track_id}")
async def delete_track(track_id: str, user: dict = Depends(get_current_user)):
    """Delete a personal upload and revoke all future streaming and room access."""
    user_id = user["sub"]
    track = db.tracks.get(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    if track.get("uploaded_by") != user_id:
        raise HTTPException(status_code=403, detail="You can only delete your own uploads")

    # Delete track record
    del db.tracks[track_id]

    # Revoke room access grants
    db.track_access = [g for g in db.track_access if g.get("track_id") != track_id]

    # Remove from all playlists
    for pid, ptracks in list(db.playlist_tracks.items()):
        db.playlist_tracks[pid] = [pt for pt in ptracks if pt.get("track_id") != track_id]

    # Remove from favorites
    for uid in db.favorites:
        db.favorites[uid].discard(track_id)

    return {"message": "Track deleted successfully and all access revoked"}
