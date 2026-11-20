"""Audio streaming endpoint with HTTP Range (206 Partial Content), Vault streaming, and access control."""

import asyncio
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from app.auth import get_optional_user, decode_app_jwt
from app.config import get_settings
from app.db import db
from app.services.audio import AudioService
from app.services.cache import FileCache
from app.services.telegram import telegram_service
from app.services.supabase_client import supabase_service

logger = logging.getLogger("streaming_router")
router = APIRouter(prefix="/api/stream", tags=["streaming"])

settings = get_settings()
audio_cache = FileCache(settings.AUDIO_CACHE_DIR, settings.MAX_AUDIO_CACHE_MB)
stream_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_STREAMS)

# Registry of in-flight download & transcode tasks to prevent duplicate downloads and survive client aborts
_in_flight_tasks: dict[str, asyncio.Task] = {}
_in_flight_lock = asyncio.Lock()


async def _download_and_transcode_track(track_id: str, quality_str: str, track: dict) -> Path:
    """
    Decoupled background worker that downloads and transcodes an uncached track to completion.
    Shielded from client HTTP disconnects so in-flight Telegram downloads are never aborted
    or deleted halfway when a browser cancels its initial metadata probe request.
    """
    cache_key = f"{track_id}_{quality_str}"
    dest_path = audio_cache.get_path(cache_key, "m4a")
    raw_codec = track.get("codec") or "flac"
    raw_cache_key = f"{track_id}_raw"
    raw_path = audio_cache.get_path(raw_cache_key, raw_codec)
    raw_was_cached = raw_path.exists() and AudioService.validate_audio_file(raw_path)

    # 1. Download raw media from Telegram Vault if not already cached
    if not raw_was_cached:
        if raw_path.exists():
            raw_path.unlink(missing_ok=True)

        chat_id = track.get("telegram_chat_id") or settings.TELEGRAM_VAULT_CHAT_ID
        message_id = track.get("telegram_message_id")

        if not chat_id or not message_id:
            logger.error(f"Track {track_id} missing Telegram Vault message reference")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Track audio reference not found in Vault"
            )

        if not telegram_service.is_connected:
            await telegram_service.initialize()
            if not telegram_service.is_connected:
                logger.error(f"Cannot stream track {track_id}: Telegram service unavailable")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Telegram audio vault service is currently unavailable"
                )

        try:
            logger.info(f"Downloading track {track_id} from Telegram chat {chat_id}, message {message_id}...")
            downloaded = await asyncio.wait_for(
                telegram_service.download_file_from_vault(chat_id, message_id, raw_path),
                timeout=240.0
            )
        except asyncio.TimeoutError:
            logger.error(f"Download timed out for track {track_id} (msg {message_id})")
            if raw_path.exists():
                raw_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Telegram Vault audio download timed out"
            )
        except Exception as e:
            logger.error(f"Download failed for track {track_id} (msg {message_id}): {type(e).__name__}: {e}")
            if raw_path.exists():
                raw_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to retrieve audio from Telegram Vault: {type(e).__name__}"
            )

        if not downloaded or not raw_path.exists():
            logger.error(
                f"Cannot transcode track {track_id}: raw audio download from Telegram Vault "
                f"(chat {chat_id}, msg {message_id}) did not produce a file on disk (expected: {raw_path})"
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Raw audio file could not be retrieved from Telegram Vault"
            )

        if not AudioService.validate_audio_file(raw_path):
            file_size = raw_path.stat().st_size if raw_path.exists() else 0
            if raw_path.exists():
                raw_path.unlink(missing_ok=True)
            logger.error(f"Downloaded media file for track {track_id} at {raw_path} ({file_size} bytes) failed media validation")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Retrieved audio file is invalid or corrupt"
            )

    # 2. Transcode raw_path to AAC M4A in temporary file
    if not raw_path.exists():
        logger.error(f"Cannot transcode track {track_id}: source raw file missing at {raw_path}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Source audio file not found on disk for transcoding"
        )

    bitrate = AudioService.get_quality_bitrate(quality_str)
    temp_transcode = dest_path.with_name(f"{dest_path.stem}.tmp_tc_{uuid.uuid4().hex[:8]}.m4a")
    try:
        success = await AudioService.transcode_to_aac(raw_path, temp_transcode, bitrate, source_codec=raw_codec)
        if not success or not temp_transcode.exists() or not AudioService.validate_audio_file(temp_transcode):
            if temp_transcode.exists():
                temp_transcode.unlink(missing_ok=True)
            logger.error(f"Audio transcoding failed for track {track_id} (source: {raw_path}, target: {dest_path})")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Audio transcoding failed to produce a valid stream"
            )

        # 3. Commit transcode to bounded LRU cache atomically
        cached_file = audio_cache.commit(
            key=cache_key,
            temp_path=temp_transcode,
            extension="m4a",
            track_id=track_id,
            quality=quality_str
        )
        return cached_file
    finally:
        if temp_transcode.exists():
            temp_transcode.unlink(missing_ok=True)
        # Note: raw_path is retained in the cache directory so subsequent quality options
        # or player requests do not require re-downloading from Telegram. Bounded LRU cache evicts as needed.


@router.get("/{track_id}")
async def stream_track(
    track_id: str,
    range_header: Optional[str] = Header(None, alias="Range"),
    quality: str = Query("standard", pattern="^(standard|high|saver)$"),
    token: Optional[str] = Query(None, description="Auth token via query parameter for <audio> element"),
    authorization: Optional[str] = Header(None)
):
    """
    Stream audio file with HTTP Range support for seamless seeking.
    Streams real audio from Telegram Vault, transcoded to AAC for universal browser playback.
    Validates audio streams and never generates or serves synthetic dummy fallback audio.
    """
    # Guard against direct parameter invocation without FastAPI dependency injection
    quality_str = quality if isinstance(quality, str) else "standard"

    # 1. Authenticate user via Header or Query param
    user_id = None
    if token:
        try:
            payload = decode_app_jwt(token)
            user_id = payload.get("sub")
        except Exception:
            pass
    elif authorization and authorization.startswith("Bearer "):
        try:
            payload = decode_app_jwt(authorization.split(" ", 1)[1])
            user_id = payload.get("sub")
        except Exception:
            pass

    # 2. Check track metadata from Supabase or memory
    track = None
    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        res = admin.table("tracks").select("*").eq("id", track_id).execute()
        if res.data:
            track = res.data[0]
    else:
        track = db.tracks.get(track_id)

    if not track:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Track not found")

    # 3. Access authorization check for private tracks
    if track.get("is_private") and track.get("uploaded_by") != user_id:
        has_access = False
        if user_id:
            if supabase_service.is_live:
                admin = supabase_service.get_admin()
                grants = admin.table("track_access").select("room_id").eq("track_id", track_id).execute()
                if grants.data:
                    room_ids = [g["room_id"] for g in grants.data]
                    rm = admin.table("room_members").select("id").in_("room_id", room_ids).eq("user_id", user_id).execute()
                    rh = admin.table("rooms").select("id").in_("id", room_ids).eq("host_id", user_id).execute()
                    if rm.data or rh.data:
                        has_access = True
            else:
                has_access = any(
                    g.get("track_id") == track_id and (user_id is not None)
                    for g in db.track_access
                )

        if not has_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: private track requires uploader ownership or active room authorization"
            )

    # 4. Limit concurrent streams
    if stream_semaphore.locked():
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Maximum concurrent audio streams reached")

    # 5. Resolve cached audio file with in-flight task deduplication and client-disconnect protection
    cache_key = f"{track_id}_{quality_str}"
    cached_file = audio_cache.get(cache_key, "m4a", validate_media=True)

    if not cached_file:
        async with _in_flight_lock:
            task = _in_flight_tasks.get(cache_key)
            if task is None or task.done():
                task = asyncio.create_task(_download_and_transcode_track(track_id, quality_str, track))
                _in_flight_tasks[cache_key] = task

        # Shield task from client cancellation so aborting an HTTP request doesn't drop the download
        try:
            cached_file = await asyncio.shield(task)
        except asyncio.CancelledError:
            # Client disconnected, but task continues downloading in background for subsequent requests
            raise
        finally:
            if task.done():
                async with _in_flight_lock:
                    if _in_flight_tasks.get(cache_key) is task:
                        _in_flight_tasks.pop(cache_key, None)

    file_size = os.path.getsize(cached_file)

    # Content type determination
    suffix = cached_file.suffix.lower()
    if suffix in (".m4a", ".mp4", ".aac"):
        content_type = "audio/mp4"
    elif suffix == ".mp3":
        content_type = "audio/mpeg"
    elif suffix == ".flac":
        content_type = "audio/flac"
    elif suffix == ".ogg":
        content_type = "audio/ogg"
    else:
        content_type = "audio/mp4"

    # 6. Handle HTTP Range Request (206 Partial Content)
    if range_header:
        match = re.search(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            start = int(match.group(1))
            end_str = match.group(2)
            end = int(end_str) if end_str else file_size - 1

            if start >= file_size:
                raise HTTPException(
                    status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                    headers={"Content-Range": f"bytes */{file_size}"}
                )
            end = min(end, file_size - 1)
            content_length = end - start + 1

            def iter_chunk():
                with audio_cache.pin_context(cache_key):
                    with open(cached_file, "rb") as f:
                        f.seek(start)
                        remaining = content_length
                        while remaining > 0:
                            chunk_size = min(remaining, 64 * 1024)
                            data = f.read(chunk_size)
                            if not data:
                                break
                            remaining -= len(data)
                            yield data

            headers = {
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
                "Content-Type": content_type,
                "Cache-Control": "public, max-age=86400",
            }
            return StreamingResponse(iter_chunk(), status_code=206, headers=headers)

    # Full file delivery
    def iter_full():
        with audio_cache.pin_context(cache_key):
            with open(cached_file, "rb") as f:
                while chunk := f.read(64 * 1024):
                    yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Content-Type": content_type,
        "Cache-Control": "public, max-age=86400",
    }
    return StreamingResponse(iter_full(), status_code=200, headers=headers)
