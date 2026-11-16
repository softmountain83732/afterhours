"""Personal music upload endpoints with metadata extraction, artwork processing, and deduplication."""

import os
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
from app.services.ingestion import IngestionService
from app.services.telegram import telegram_service

router = APIRouter(prefix="/api/uploads", tags=["uploads"])
settings = get_settings()


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
    Extracts metadata, artwork, computes SHA-256, deduplicates, and ingests into Vault.
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
        for existing in db.tracks.values():
            if existing.get("file_hash") == file_hash:
                temp_path.unlink(missing_ok=True)
                # If already uploaded by this user, return it
                if existing.get("uploaded_by") == user_id:
                    return {
                        "message": "File is already in your uploads",
                        "track": existing,
                        "is_duplicate": True
                    }
                # Else return reference associated to user
                new_track_id = str(uuid.uuid4())
                duplicate_track = {
                    **existing,
                    "id": new_track_id,
                    "uploaded_by": user_id,
                    "is_private": True,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                db.tracks[new_track_id] = duplicate_track
                return {
                    "message": "Duplicate file detected. Track registered instantly from Vault cache.",
                    "track": duplicate_track,
                    "is_duplicate": True
                }

        # 3. Extract audio metadata using Mutagen
        meta = IngestionService.extract_metadata(temp_path)

        # 4. Extract embedded artwork and optimize
        artwork_bytes = IngestionService.extract_artwork(temp_path)
        artwork_url = "https://images.unsplash.com/photo-1514525253161-7a46d19cd819?w=800&q=80"
        artwork_thumb_url = "https://images.unsplash.com/photo-1514525253161-7a46d19cd819?w=300&q=80"

        if artwork_bytes:
            try:
                full_webp, thumb_webp = IngestionService.optimize_artwork(artwork_bytes)
                img_hash = IngestionService.compute_image_hash(artwork_bytes)

                # Save optimized artwork to cache
                art_dir = Path(settings.ARTWORK_CACHE_DIR)
                art_dir.mkdir(parents=True, exist_ok=True)
                full_art_path = art_dir / f"{img_hash}_full.webp"
                thumb_art_path = art_dir / f"{img_hash}_thumb.webp"

                with open(full_art_path, "wb") as f:
                    f.write(full_webp)
                with open(thumb_art_path, "wb") as f:
                    f.write(thumb_webp)

                # In production, upload to Supabase Storage; in local dev, provide relative /api/static path
                artwork_url = f"/api/static/artwork/{img_hash}_full.webp"
                artwork_thumb_url = f"/api/static/artwork/{img_hash}_thumb.webp"
            except Exception:
                pass

        # 5. Ingest into Telegram Vault
        vault_ref = await telegram_service.send_to_vault(
            temp_path,
            title=meta.get("title"),
            artist=meta.get("artist"),
            duration=int(meta.get("duration_ms", 0) / 1000)
        )

        chat_id = vault_ref.get("chat_id") if vault_ref else settings.TELEGRAM_VAULT_CHAT_ID
        message_id = vault_ref.get("message_id") if vault_ref else int(datetime.now().timestamp())

        # 6. Save artist & album if needed
        artist_name = meta.get("artist") or "Unknown Artist"
        album_name = meta.get("album") or "Unknown Album"

        # Check existing artist
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

        # 7. Register Track
        track_id = str(uuid.uuid4())
        track = {
            "id": track_id,
            "title": meta.get("title", file.filename or "Untitled Track"),
            "artist_id": artist_id,
            "artist_name": artist_name,
            "album_id": album_id,
            "album_title": album_name,
            "artwork_url": artwork_url,
            "artwork_thumb_url": artwork_thumb_url,
            "duration_ms": meta.get("duration_ms", 0),
            "track_number": meta.get("track_number"),
            "codec": meta.get("codec", suffix.lstrip(".")),
            "bitrate": meta.get("bitrate"),
            "file_hash": file_hash,
            "telegram_chat_id": chat_id,
            "telegram_message_id": message_id,
            "uploaded_by": user_id,
            "is_private": True,  # Private to uploader by default
            "file_size": file_size,
            "ingestion_status": "completed",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        db.tracks[track_id] = track

        return {
            "message": "Track successfully uploaded and ingested into Afterhours Vault",
            "track": track,
            "is_duplicate": False
        }

    finally:
        temp_path.unlink(missing_ok=True)
