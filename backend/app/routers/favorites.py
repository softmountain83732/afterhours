"""Favorites management endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from app.auth import get_current_user
from app.db import db
from app.routers.tracks import format_track
from app.services.supabase_client import supabase_service

router = APIRouter(prefix="/api/favorites", tags=["favorites"])


@router.get("")
async def list_favorites(user: dict = Depends(get_current_user)):
    """List current user's favorite tracks."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        res = admin.table("favorites").select(
            "track_id, tracks(*, artists(id, name), albums(id, title, artwork_url, artwork_thumb_url), track_artwork(original_url, thumb_url))"
        ).eq("user_id", user_id).order("created_at", desc=True).execute()

        results = []
        for row in (res.data or []):
            raw_track = row.get("tracks")
            if raw_track:
                results.append(format_track(raw_track, is_favorite=True))
        return results

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

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        track_res = admin.table("tracks").select("id").eq("id", track_id).execute()
        if not track_res.data:
            raise HTTPException(status_code=404, detail="Track not found")

        # Upsert into favorites
        admin.table("favorites").upsert({
            "user_id": user_id,
            "track_id": track_id,
        }, on_conflict="user_id,track_id").execute()
    else:
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

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        admin.table("favorites").delete().eq("user_id", user_id).eq("track_id", track_id).execute()

    user_favs = db.favorites.get(user_id, set())
    user_favs.discard(track_id)
    return {"message": "Track removed from favorites", "track_id": track_id}
