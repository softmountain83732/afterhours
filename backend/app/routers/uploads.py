"""Personal music upload endpoints with metadata extraction, artwork processing, and deduplication."""

import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.auth import get_current_user
from app.config import get_settings
from app.db import db
from app.routers.tracks import format_track
from app.services.cache import FileCache
from app.services.ingestion import IngestionService
from app.services.telegram import telegram_service
from app.services.supabase_client import supabase_service
from app.services.track_cache import track_response_cache

router = APIRouter(prefix="/api/uploads", tags=["uploads"])
settings = get_settings()
audio_cache = FileCache(settings.AUDIO_CACHE_DIR, settings.MAX_CACHE_SIZE_MB)


class ConfirmRightsRequest(BaseModel):
    has_permission: bool


@router.post("/file")
async def upload_audio_file(
    file: UploadFile = File(...),
    has_permission: bool = Form(..., description="Confirmation that uploader holds legal right or permission"),
    user: dict = Depends(get_current_user)
):
    """
    Upload a permitted music file (FLAC, MP3, M4A, WAV, etc.).
    Extracts metadata, artwork, computes SHA-256, deduplicates, and ingests into Vault & Supabase.
    """
    if user.get("is_guest"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Guest users cannot upload music. Please register an account."
        )

    if not has_permission:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must confirm you have the legal right or permission to store and share this audio file."
        )

    # Validate file extension
    suffix = Path(file.filename or "audio.mp3").suffix.lower()
    allowed_extensions = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".wav", ".aiff"}
    if suffix not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported audio format '{suffix}'. Allowed: {', '.join(allowed_extensions)}"
        )

    user_id = user["sub"]

    # Save to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_path = Path(temp_file.name)
        file_size = 0
        max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

        while chunk := await file.read(65536):
            file_size += len(chunk)
            if file_size > max_bytes:
                temp_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File exceeds maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB}MB"
                )
            temp_file.write(chunk)

    try:
        # 1. Compute SHA-256 for exact duplicate detection
        file_hash = IngestionService.compute_file_hash(temp_path)

        # 2. Check for exact duplicate in database
        if supabase_service.is_live:
            admin = supabase_service.get_admin()
            dup_res = admin.table("tracks").select(
                "*, artists(name), albums(title, artwork_url, artwork_thumb_url), track_artwork(original_url, thumb_url)"
            ).eq("file_hash", file_hash).execute()

            if dup_res.data:
                existing = dup_res.data[0]
                formatted = format_track(existing, is_favorite=False)
                if existing.get("uploaded_by") == user_id:
                    return {
                        "message": "File is already in your uploads",
                        "track": formatted,
                        "is_duplicate": True
                    }
                return {
                    "message": "Duplicate file detected in Vault. Reusing existing audio storage.",
                    "track": formatted,
                    "is_duplicate": True
                }
        else:
            for existing in db.tracks.values():
                if existing.get("file_hash") == file_hash:
                    if existing.get("uploaded_by") == user_id:
                        return {
                            "message": "File is already in your uploads",
                            "track": existing,
                            "is_duplicate": True
                        }
                    new_track_id = str(uuid.uuid4())
                    duplicate_track = {
                        **existing,
                        "id": new_track_id,
                        "uploaded_by": user_id,
                        "is_private": True,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                    db.tracks[new_track_id] = duplicate_track
                    track_response_cache.invalidate()
                    return {
                        "message": "Duplicate file detected. Track registered instantly from Vault cache.",
                        "track": duplicate_track,
                        "is_duplicate": True
                    }

        # 3. Extract audio metadata using Mutagen
        meta = IngestionService.extract_metadata(temp_path)

        # 4. Extract embedded artwork and optimize
        artwork_bytes = IngestionService.extract_artwork(temp_path)
        img_hash = None
        artwork_url = "/api/static/artwork/default.svg"
        artwork_thumb_url = "/api/static/artwork/default.svg"

        if artwork_bytes:
            try:
                full_webp, thumb_webp = IngestionService.optimize_artwork(artwork_bytes)
                img_hash = IngestionService.compute_image_hash(artwork_bytes)

                art_dir = Path(settings.ARTWORK_CACHE_DIR)
                art_dir.mkdir(parents=True, exist_ok=True)
                full_art_path = art_dir / f"{img_hash}_full.webp"
                thumb_art_path = art_dir / f"{img_hash}_thumb.webp"

                with open(full_art_path, "wb") as f:
                    f.write(full_webp)
                with open(thumb_art_path, "wb") as f:
                    f.write(thumb_webp)

                artwork_url = f"/api/static/artwork/{img_hash}_full.webp"
                artwork_thumb_url = f"/api/static/artwork/{img_hash}_thumb.webp"

                if supabase_service.is_live:
                    admin = supabase_service.get_admin()
                    if admin:
                        try:
                            try:
                                admin.storage.create_bucket("artwork", options={"public": True})
                            except Exception:
                                pass
                            admin.storage.from_("artwork").upload(
                                path=f"{img_hash}_full.webp",
                                file=full_webp,
                                file_options={"content-type": "image/webp", "upsert": "true"}
                            )
                            admin.storage.from_("artwork").upload(
                                path=f"{img_hash}_thumb.webp",
                                file=thumb_webp,
                                file_options={"content-type": "image/webp", "upsert": "true"}
                            )
                            artwork_url = admin.storage.from_("artwork").get_public_url(f"{img_hash}_full.webp")
                            artwork_thumb_url = admin.storage.from_("artwork").get_public_url(f"{img_hash}_thumb.webp")
                        except Exception:
                            pass
            except Exception:
                pass

        # 5. Ingest into Telegram Vault
        title_str = meta.get("title") or Path(file.filename or "track").stem
        artist_name = meta.get("artist") or "Unknown Artist"
        album_name = meta.get("album") or "Unknown Album"
        duration_sec = int(meta.get("duration_ms", 0) / 1000)

        vault_ref = await telegram_service.send_to_vault(
            temp_path,
            title=title_str,
            artist=artist_name,
            duration=duration_sec
        )

        chat_id = vault_ref.get("chat_id") if vault_ref else settings.TELEGRAM_VAULT_CHAT_ID
        message_id = vault_ref.get("message_id") if vault_ref else int(datetime.now().timestamp())

        # 6. Database ingestion
        track_id = str(uuid.uuid4())
        codec_str = meta.get("codec") or suffix.lstrip(".")

        if supabase_service.is_live:
            admin = supabase_service.get_admin()

            # Artist
            art_res = admin.table("artists").select("id").ilike("name", artist_name).execute()
            if art_res.data:
                artist_id = art_res.data[0]["id"]
            else:
                new_art = admin.table("artists").insert({"name": artist_name}).execute()
                artist_id = new_art.data[0]["id"] if new_art.data else str(uuid.uuid4())

            # Album
            alb_res = admin.table("albums").select("id").ilike("title", album_name).eq("artist_id", artist_id).execute()
            if alb_res.data:
                album_id = alb_res.data[0]["id"]
            else:
                new_alb = admin.table("albums").insert({
                    "title": album_name,
                    "artist_id": artist_id,
                    "artwork_url": artwork_url,
                    "artwork_thumb_url": artwork_thumb_url,
                }).execute()
                album_id = new_alb.data[0]["id"] if new_alb.data else str(uuid.uuid4())

            # Track
            track_row = {
                "id": track_id,
                "title": title_str,
                "artist_id": artist_id,
                "album_id": album_id,
                "duration_ms": meta.get("duration_ms", 0),
                "track_number": meta.get("track_number"),
                "codec": codec_str,
                "bitrate": meta.get("bitrate"),
                "file_hash": file_hash,
                "telegram_chat_id": chat_id,
                "telegram_message_id": message_id,
                "uploaded_by": user_id,
                "is_private": True,
                "file_size": file_size,
                "metadata_raw": meta,
                "ingestion_status": "completed",
            }
            admin.table("tracks").insert(track_row).execute()

            # Artwork
            if artwork_bytes and img_hash:
                try:
                    admin.table("track_artwork").insert({
                        "track_id": track_id,
                        "image_hash": img_hash,
                        "original_url": artwork_url,
                        "thumb_url": artwork_thumb_url,
                    }).execute()
                except Exception:
                    pass

            # Pre-seed raw audio cache so it is immediately streamable without re-downloading
            raw_path = audio_cache.get_path(f"{track_id}_raw", codec_str)
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(temp_path, raw_path)

            formatted = {
                "id": track_id,
                "title": title_str,
                "artist_id": artist_id,
                "artist_name": artist_name,
                "album_id": album_id,
                "album_title": album_name,
                "artwork_url": artwork_url,
                "artwork_thumb_url": artwork_thumb_url,
                "duration_ms": meta.get("duration_ms", 0),
                "track_number": meta.get("track_number"),
                "codec": codec_str,
                "bitrate": meta.get("bitrate"),
                "file_hash": file_hash,
                "telegram_chat_id": chat_id,
                "telegram_message_id": message_id,
                "uploaded_by": user_id,
                "is_private": True,
                "file_size": file_size,
                "ingestion_status": "completed",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "is_favorite": False,
            }

            track_response_cache.invalidate()
            return {
                "message": "Track successfully uploaded and ingested into Afterhours Vault",
                "track": formatted,
                "is_duplicate": False,
            }

        # Fallback memory
        artist_id = None
        for a in db.artists.values():
            if a.get("name", "").lower() == artist_name.lower():
                artist_id = a["id"]
                break
        if not artist_id:
            artist_id = str(uuid.uuid4())
            db.artists[artist_id] = {
                "id": artist_id,
                "name": artist_name,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        album_id = None
        for al in db.albums.values():
            if al.get("title", "").lower() == album_name.lower() and al.get("artist_id") == artist_id:
                album_id = al["id"]
                break
        if not album_id:
            album_id = str(uuid.uuid4())
            db.albums[album_id] = {
                "id": album_id,
                "title": album_name,
                "artist_id": artist_id,
                "artwork_url": artwork_url,
                "artwork_thumb_url": artwork_thumb_url,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        track = {
            "id": track_id,
            "title": title_str,
            "artist_id": artist_id,
            "artist_name": artist_name,
            "album_id": album_id,
            "album_title": album_name,
            "artwork_url": artwork_url,
            "artwork_thumb_url": artwork_thumb_url,
            "duration_ms": meta.get("duration_ms", 0),
            "track_number": meta.get("track_number"),
            "codec": codec_str,
            "bitrate": meta.get("bitrate"),
            "file_hash": file_hash,
            "telegram_chat_id": chat_id,
            "telegram_message_id": message_id,
            "uploaded_by": user_id,
            "is_private": True,
            "file_size": file_size,
            "ingestion_status": "completed",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        db.tracks[track_id] = track
        track_response_cache.invalidate()

        return {
            "message": "Track successfully uploaded and ingested into Afterhours Vault",
            "track": track,
            "is_duplicate": False,
        }

    finally:
        temp_path.unlink(missing_ok=True)
