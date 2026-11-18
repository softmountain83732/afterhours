"""Tracks and music discovery endpoints."""

from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import get_current_user, get_optional_user
from app.db import db
from app.services.supabase_client import supabase_service

router = APIRouter(prefix="/api/tracks", tags=["tracks"])


def format_track(t: Dict[str, Any], is_favorite: bool = False) -> Dict[str, Any]:
    """Format track record with joined artist, album, and artwork into standard frontend schema."""
    artist_name = "Unknown Artist"
    if t.get("artists") and isinstance(t["artists"], dict):
        artist_name = t["artists"].get("name") or "Unknown Artist"
    elif t.get("artist_name"):
        artist_name = t["artist_name"]

    album_title = "Unknown Album"
    album_artwork_url = None
    album_artwork_thumb_url = None
    if t.get("albums") and isinstance(t["albums"], dict):
        album_title = t["albums"].get("title") or "Unknown Album"
        album_artwork_url = t["albums"].get("artwork_url")
        album_artwork_thumb_url = t["albums"].get("artwork_thumb_url")
    elif t.get("album_title"):
        album_title = t["album_title"]

    art_orig = None
    art_thumb = None
    if t.get("track_artwork") and isinstance(t["track_artwork"], list) and len(t["track_artwork"]) > 0:
        art_orig = t["track_artwork"][0].get("original_url")
        art_thumb = t["track_artwork"][0].get("thumb_url")

    artwork_url = art_orig or album_artwork_url or t.get("artwork_url")
    artwork_thumb_url = art_thumb or album_artwork_thumb_url or t.get("artwork_thumb_url")

    return {
        "id": t["id"],
        "title": t["title"],
        "artist_id": t.get("artist_id"),
        "artist_name": artist_name,
        "album_id": t.get("album_id"),
        "album_title": album_title,
        "artwork_url": artwork_url,
        "artwork_thumb_url": artwork_thumb_url,
        "duration_ms": t.get("duration_ms", 0),
        "track_number": t.get("track_number"),
        "codec": t.get("codec") or "flac",
        "bitrate": t.get("bitrate"),
        "file_hash": t.get("file_hash") or "",
        "telegram_chat_id": t.get("telegram_chat_id"),
        "telegram_message_id": t.get("telegram_message_id"),
        "uploaded_by": t.get("uploaded_by"),
        "is_private": bool(t.get("is_private", False)),
        "file_size": t.get("file_size"),
        "ingestion_status": t.get("ingestion_status", "completed"),
        "created_at": t.get("created_at"),
        "updated_at": t.get("updated_at"),
        "is_favorite": is_favorite,
    }


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

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        query = admin.table("tracks").select(
            "*, artists(id, name), albums(id, title, artwork_url, artwork_thumb_url), track_artwork(original_url, thumb_url)"
        )
        if user_id:
            query = query.or_(f"is_private.eq.false,uploaded_by.eq.{user_id}")
        else:
            query = query.eq("is_private", False)

        res = query.order("created_at", desc=True).execute()
        raw_tracks = res.data or []

        # User favorites
        user_favs = set()
        if user_id:
            fav_res = admin.table("favorites").select("track_id").eq("user_id", user_id).execute()
            user_favs = {row["track_id"] for row in (fav_res.data or [])}

        formatted = [format_track(t, is_favorite=t["id"] in user_favs) for t in raw_tracks]

        # Search filter
        if q and q.strip():
            term = q.strip().lower()
            formatted = [
                t for t in formatted
                if term in t["title"].lower()
                or term in t["artist_name"].lower()
                or term in t["album_title"].lower()
            ]

        total = len(formatted)
        paged = formatted[offset: offset + limit]

        return {
            "items": paged,
            "total": total,
            "limit": limit,
            "offset": offset
        }

    # Fallback in-memory
    accessible = []
    for t in db.tracks.values():
        if not t.get("is_private") or (user_id and t.get("uploaded_by") == user_id):
            accessible.append(t)

    if q and q.strip():
        term = q.strip().lower()
        accessible = [
            t for t in accessible
            if term in t.get("title", "").lower()
            or term in t.get("artist_name", "").lower()
            or term in t.get("album_title", "").lower()
        ]

    total = len(accessible)
    paged = accessible[offset: offset + limit]
    user_favs = db.favorites.get(user_id, set()) if user_id else set()

    return {
        "items": [{**t, "is_favorite": t["id"] in user_favs} for t in paged],
        "total": total,
        "limit": limit,
        "offset": offset
    }


@router.get("/my-uploads")
async def get_my_uploads(user: dict = Depends(get_current_user)):
    """Get all personal music uploads by current user."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        res = admin.table("tracks").select(
            "*, artists(id, name), albums(id, title, artwork_url, artwork_thumb_url), track_artwork(original_url, thumb_url)"
        ).eq("uploaded_by", user_id).order("created_at", desc=True).execute()

        fav_res = admin.table("favorites").select("track_id").eq("user_id", user_id).execute()
        user_favs = {row["track_id"] for row in (fav_res.data or [])}

        return [format_track(t, is_favorite=t["id"] in user_favs) for t in (res.data or [])]

    my_tracks = [t for t in db.tracks.values() if t.get("uploaded_by") == user_id]
    user_favs = db.favorites.get(user_id, set())
    return [{**t, "is_favorite": t["id"] in user_favs} for t in my_tracks]


@router.get("/{track_id}")
async def get_track(track_id: str, user: Optional[dict] = Depends(get_optional_user)):
    """Get single track metadata."""
    user_id = user.get("sub") if user else None

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        res = admin.table("tracks").select(
            "*, artists(id, name), albums(id, title, artwork_url, artwork_thumb_url), track_artwork(original_url, thumb_url)"
        ).eq("id", track_id).execute()

        if not res.data:
            raise HTTPException(status_code=404, detail="Track not found")

        raw_track = res.data[0]
        if raw_track.get("is_private") and raw_track.get("uploaded_by") != user_id:
            # Check room grant
            has_grant = False
            if user_id:
                grants = admin.table("track_access").select("room_id").eq("track_id", track_id).execute()
                if grants.data:
                    room_ids = [g["room_id"] for g in grants.data]
                    rm = admin.table("room_members").select("id").in_("room_id", room_ids).eq("user_id", user_id).execute()
                    rh = admin.table("rooms").select("id").in_("id", room_ids).eq("host_id", user_id).execute()
                    if rm.data or rh.data:
                        has_grant = True
            if not has_grant:
                raise HTTPException(status_code=403, detail="Access denied to private track")

        user_favs = set()
        if user_id:
            fav_res = admin.table("favorites").select("track_id").eq("user_id", user_id).execute()
            user_favs = {row["track_id"] for row in (fav_res.data or [])}

        return format_track(raw_track, is_favorite=track_id in user_favs)

    track = db.tracks.get(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    if track.get("is_private") and track.get("uploaded_by") != user_id:
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

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        res = admin.table("tracks").select("id, uploaded_by").eq("id", track_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Track not found")

        if res.data[0].get("uploaded_by") != user_id:
            raise HTTPException(status_code=403, detail="You can only delete your own uploads")

        admin.table("tracks").delete().eq("id", track_id).execute()

    if track_id in db.tracks:
        if db.tracks[track_id].get("uploaded_by") != user_id:
            raise HTTPException(status_code=403, detail="You can only delete your own uploads")
        del db.tracks[track_id]

    db.track_access = [g for g in db.track_access if g.get("track_id") != track_id]
    for pid, ptracks in list(db.playlist_tracks.items()):
        db.playlist_tracks[pid] = [pt for pt in ptracks if pt.get("track_id") != track_id]
    for uid in db.favorites:
        db.favorites[uid].discard(track_id)

    return {"message": "Track deleted successfully and all access revoked"}
