"""Playlist management endpoints."""

import uuid
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import get_current_user, get_optional_user
from app.db import db
from app.routers.tracks import format_track
from app.services.supabase_client import supabase_service

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


class PlaylistCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    is_public: bool = False


class PlaylistUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    is_public: Optional[bool] = None


class AddTrackRequest(BaseModel):
    track_id: str


class ReorderTracksRequest(BaseModel):
    track_ids: List[str]


@router.get("")
async def list_playlists(user: dict = Depends(get_current_user)):
    """List current user's playlists."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        pl_res = admin.table("playlists").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()

        result = []
        for p in (pl_res.data or []):
            pt_res = admin.table("playlist_tracks").select(
                "position, tracks(id, artwork_url, albums(artwork_thumb_url, artwork_url), track_artwork(thumb_url, original_url))"
            ).eq("playlist_id", p["id"]).order("position", desc=False).execute()

            track_count = len(pt_res.data or [])
            preview_artwork = None
            if pt_res.data:
                first_tr = pt_res.data[0].get("tracks")
                if first_tr:
                    f_art = first_tr.get("track_artwork")
                    if f_art and len(f_art) > 0:
                        preview_artwork = f_art[0].get("thumb_url") or f_art[0].get("original_url")
                    if not preview_artwork:
                        f_alb = first_tr.get("albums")
                        if f_alb:
                            preview_artwork = f_alb.get("artwork_thumb_url") or f_alb.get("artwork_url")
                    if not preview_artwork:
                        preview_artwork = first_tr.get("artwork_url")

            result.append({
                **p,
                "track_count": track_count,
                "preview_artwork": preview_artwork
            })

        return result

    user_playlists = [p for p in db.playlists.values() if p.get("user_id") == user_id]
    result = []
    for p in user_playlists:
        tracks = db.playlist_tracks.get(p["id"], [])
        preview_artwork = None
        if tracks:
            first_track_id = tracks[0].get("track_id")
            first_track = db.tracks.get(first_track_id)
            if first_track:
                preview_artwork = first_track.get("artwork_thumb_url") or first_track.get("artwork_url")

        result.append({
            **p,
            "track_count": len(tracks),
            "preview_artwork": preview_artwork
        })

    return result


@router.post("")
async def create_playlist(req: PlaylistCreateRequest, user: dict = Depends(get_current_user)):
    """Create a new playlist."""
    if user.get("is_guest"):
        raise HTTPException(status_code=403, detail="Guests cannot create playlists")

    user_id = user["sub"]
    playlist_id = str(uuid.uuid4())

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        new_p = {
            "id": playlist_id,
            "user_id": user_id,
            "name": req.name.strip(),
            "description": req.description.strip() if req.description else "",
            "is_public": req.is_public,
        }
        res = admin.table("playlists").insert(new_p).execute()
        created = res.data[0] if res.data else new_p
        db.playlists[playlist_id] = created
        db.playlist_tracks[playlist_id] = []
        return created

    new_playlist = {
        "id": playlist_id,
        "user_id": user_id,
        "name": req.name.strip(),
        "description": req.description.strip() if req.description else "",
        "is_public": req.is_public,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    db.playlists[playlist_id] = new_playlist
    db.playlist_tracks[playlist_id] = []

    return new_playlist


@router.get("/{playlist_id}")
async def get_playlist(playlist_id: str, user: Optional[dict] = Depends(get_optional_user)):
    """Get playlist details and its tracks."""
    user_id = user.get("sub") if user else None

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        p_res = admin.table("playlists").select("*").eq("id", playlist_id).execute()
        if not p_res.data:
            raise HTTPException(status_code=404, detail="Playlist not found")
        playlist = p_res.data[0]

        if not playlist.get("is_public") and playlist.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Access denied to private playlist")

        pt_res = admin.table("playlist_tracks").select(
            "position, tracks(*, artists(id, name), albums(id, title, artwork_url, artwork_thumb_url), track_artwork(original_url, thumb_url))"
        ).eq("playlist_id", playlist_id).order("position", desc=False).execute()

        user_favs = set()
        if user_id:
            fav_res = admin.table("favorites").select("track_id").eq("user_id", user_id).execute()
            user_favs = {row["track_id"] for row in (fav_res.data or [])}

        tracks_details = []
        for item in (pt_res.data or []):
            raw_t = item.get("tracks")
            if raw_t:
                formatted = format_track(raw_t, is_favorite=raw_t["id"] in user_favs)
                formatted["position"] = item.get("position", 0)
                tracks_details.append(formatted)

        return {
            **playlist,
            "tracks": tracks_details,
            "is_owner": playlist.get("user_id") == user_id
        }

    playlist = db.playlists.get(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    if not playlist.get("is_public") and playlist.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied to private playlist")

    raw_tracks = db.playlist_tracks.get(playlist_id, [])
    tracks_details = []
    user_favs = db.favorites.get(user_id, set()) if user_id else set()

    for item in raw_tracks:
        tid = item.get("track_id")
        t = db.tracks.get(tid)
        if t:
            tracks_details.append({
                **t,
                "position": item.get("position", 0),
                "is_favorite": tid in user_favs
            })

    return {
        **playlist,
        "tracks": tracks_details,
        "is_owner": playlist.get("user_id") == user_id
    }


@router.patch("/{playlist_id}")
async def update_playlist(playlist_id: str, req: PlaylistUpdateRequest, user: dict = Depends(get_current_user)):
    """Update playlist info (owner only)."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        p_res = admin.table("playlists").select("*").eq("id", playlist_id).execute()
        if not p_res.data:
            raise HTTPException(status_code=404, detail="Playlist not found")
        playlist = p_res.data[0]
        if playlist.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="You can only edit your own playlist")

        up_data = {}
        if req.name is not None:
            up_data["name"] = req.name.strip()
        if req.description is not None:
            up_data["description"] = req.description.strip()
        if req.is_public is not None:
            up_data["is_public"] = req.is_public

        res = admin.table("playlists").update(up_data).eq("id", playlist_id).execute()
        return res.data[0] if res.data else {**playlist, **up_data}

    playlist = db.playlists.get(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    if playlist.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="You can only edit your own playlist")

    if req.name is not None:
        playlist["name"] = req.name.strip()
    if req.description is not None:
        playlist["description"] = req.description.strip()
    if req.is_public is not None:
        playlist["is_public"] = req.is_public

    playlist["updated_at"] = datetime.now(timezone.utc).isoformat()
    db.playlists[playlist_id] = playlist
    return playlist


@router.delete("/{playlist_id}")
async def delete_playlist(playlist_id: str, user: dict = Depends(get_current_user)):
    """Delete a playlist (owner only)."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        p_res = admin.table("playlists").select("id, user_id").eq("id", playlist_id).execute()
        if not p_res.data:
            raise HTTPException(status_code=404, detail="Playlist not found")
        if p_res.data[0].get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="You can only delete your own playlist")

        admin.table("playlists").delete().eq("id", playlist_id).execute()

    if playlist_id in db.playlists:
        if db.playlists[playlist_id].get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="You can only delete your own playlist")
        del db.playlists[playlist_id]
        if playlist_id in db.playlist_tracks:
            del db.playlist_tracks[playlist_id]

    return {"message": "Playlist deleted successfully"}


@router.post("/{playlist_id}/tracks")
async def add_track_to_playlist(playlist_id: str, req: AddTrackRequest, user: dict = Depends(get_current_user)):
    """Add a track to playlist."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        p_res = admin.table("playlists").select("id, user_id").eq("id", playlist_id).execute()
        if not p_res.data:
            raise HTTPException(status_code=404, detail="Playlist not found")
        if p_res.data[0].get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="You can only modify your own playlist")

        track_res = admin.table("tracks").select("id").eq("id", req.track_id).execute()
        if not track_res.data:
            raise HTTPException(status_code=404, detail="Track not found")

        # Existing tracks in playlist
        cur_res = admin.table("playlist_tracks").select("id, position, track_id").eq("playlist_id", playlist_id).execute()
        cur_tracks = cur_res.data or []
        if any(pt.get("track_id") == req.track_id for pt in cur_tracks):
            raise HTTPException(status_code=400, detail="Track is already in playlist")

        new_pos = len(cur_tracks)
        admin.table("playlist_tracks").insert({
            "playlist_id": playlist_id,
            "track_id": req.track_id,
            "position": new_pos
        }).execute()

        return {"message": "Track added to playlist", "position": new_pos}

    playlist = db.playlists.get(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    if playlist.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="You can only modify your own playlist")

    track = db.tracks.get(req.track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    tracks = db.playlist_tracks.setdefault(playlist_id, [])
    if any(pt.get("track_id") == req.track_id for pt in tracks):
        raise HTTPException(status_code=400, detail="Track is already in playlist")

    new_pos = len(tracks)
    tracks.append({
        "track_id": req.track_id,
        "position": new_pos,
        "added_at": datetime.now(timezone.utc).isoformat()
    })

    return {"message": "Track added to playlist", "position": new_pos}


@router.delete("/{playlist_id}/tracks/{track_id}")
async def remove_track_from_playlist(playlist_id: str, track_id: str, user: dict = Depends(get_current_user)):
    """Remove a track from playlist."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        p_res = admin.table("playlists").select("id, user_id").eq("id", playlist_id).execute()
        if not p_res.data:
            raise HTTPException(status_code=404, detail="Playlist not found")
        if p_res.data[0].get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="You can only modify your own playlist")

        admin.table("playlist_tracks").delete().eq("playlist_id", playlist_id).eq("track_id", track_id).execute()
        return {"message": "Track removed from playlist"}

    playlist = db.playlists.get(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    if playlist.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="You can only modify your own playlist")

    tracks = db.playlist_tracks.get(playlist_id, [])
    updated = [pt for pt in tracks if pt.get("track_id") != track_id]
    for idx, item in enumerate(updated):
        item["position"] = idx

    db.playlist_tracks[playlist_id] = updated
    return {"message": "Track removed from playlist"}


@router.patch("/{playlist_id}/tracks/reorder")
async def reorder_playlist_tracks(playlist_id: str, req: ReorderTracksRequest, user: dict = Depends(get_current_user)):
    """Reorder tracks in playlist."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        p_res = admin.table("playlists").select("id, user_id").eq("id", playlist_id).execute()
        if not p_res.data:
            raise HTTPException(status_code=404, detail="Playlist not found")
        if p_res.data[0].get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="You can only modify your own playlist")

        for idx, tid in enumerate(req.track_ids):
            admin.table("playlist_tracks").update({"position": idx}).eq("playlist_id", playlist_id).eq("track_id", tid).execute()

        return {"message": "Tracks reordered successfully"}

    playlist = db.playlists.get(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    if playlist.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="You can only modify your own playlist")

    tracks_by_id = {pt["track_id"]: pt for pt in db.playlist_tracks.get(playlist_id, [])}
    new_tracks = []
    for idx, tid in enumerate(req.track_ids):
        if tid in tracks_by_id:
            item = tracks_by_id[tid]
            item["position"] = idx
            new_tracks.append(item)

    db.playlist_tracks[playlist_id] = new_tracks
    return {"message": "Tracks reordered successfully"}
