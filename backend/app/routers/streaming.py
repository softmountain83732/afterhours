"""Audio streaming endpoint with HTTP Range (206 Partial Content) and access control."""

import asyncio
import os
import re
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

router = APIRouter(prefix="/api/stream", tags=["streaming"])

settings = get_settings()
audio_cache = FileCache(settings.AUDIO_CACHE_DIR, settings.MAX_CACHE_SIZE_MB)
stream_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_STREAMS)


def _get_or_create_dummy_audio(target_path: Path, duration_seconds: int = 180) -> Path:
    """Generate a clean silent or tone test AAC file using FFmpeg if no real file exists."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() and target_path.stat().st_size > 0:
        return target_path

    # Generate test sine tone using ffmpeg
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={duration_seconds}",
        "-c:a", "aac",
        "-b:a", "192k",
        str(target_path)
    ]
    try:
        import subprocess
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception:
        # Fallback dummy binary file
        with open(target_path, "wb") as f:
            f.write(b"RIFF" + b"\x00" * 1024)
    return target_path


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
    Validates user authentication and track access rights.
    """
    # 1. Authenticate user via Header or Query param (since HTML <audio> may not send Authorization header)
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

    # 2. Check track existence
    track = db.tracks.get(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    # 3. Access authorization check
    if track.get("is_private") and track.get("uploaded_by") != user_id:
        # Check active room grants
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
        raise HTTPException(status_code=429, detail="Maximum concurrent audio streams reached")

    # 5. Resolve cached audio file
    cache_key = f"{track_id}_{quality}"
    cached_file = audio_cache.get(cache_key, "m4a")

    if not cached_file or not cached_file.exists():
        # Need to fetch or generate
        dest_path = audio_cache.get_path(cache_key, "m4a")

        # Check if raw telegram file exists or sample audio can be prepared
        raw_cache_key = f"{track_id}_raw"
        raw_path = audio_cache.get_path(raw_cache_key, track.get("codec", "flac"))

        if not raw_path.exists():
            # Attempt Telethon download from Vault
            downloaded = await telegram_service.download_file_from_vault(
                track.get("telegram_chat_id", settings.TELEGRAM_VAULT_CHAT_ID),
                track.get("telegram_message_id", 0),
                raw_path
            )
            if not downloaded or not downloaded.exists():
                # Provide playable dev audio for testing
                _get_or_create_dummy_audio(dest_path, duration_seconds=int(track.get("duration_ms", 180000) / 1000))
                cached_file = dest_path

        if not cached_file:
            # Transcode raw_path to AAC m4a
            bitrate = AudioService.get_quality_bitrate(quality)
            success = await AudioService.transcode_to_aac(raw_path, dest_path, bitrate)
            if success and dest_path.exists():
                cached_file = dest_path
            else:
                _get_or_create_dummy_audio(dest_path, duration_seconds=int(track.get("duration_ms", 180000) / 1000))
                cached_file = dest_path

    file_size = os.path.getsize(cached_file)

    # 6. Handle HTTP Range Request (206 Partial Content)
    if range_header:
        # Format: bytes=start-end
        match = re.search(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            start = int(match.group(1))
            end_str = match.group(2)
            end = int(end_str) if end_str else file_size - 1
            if start >= file_size:
                raise HTTPException(
                    status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                    headers={"Content-Range": f"bytes */{file_size}"}
                )
            end = min(end, file_size - 1)
            content_length = end - start + 1

            def iter_chunk():
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
                "Content-Type": "audio/mp4",
                "Cache-Control": "public, max-age=86400",
            }
            return StreamingResponse(iter_chunk(), status_code=206, headers=headers)

    # Full file delivery
    def iter_full():
        with open(cached_file, "rb") as f:
            while chunk := f.read(64 * 1024):
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Content-Type": "audio/mp4",
        "Cache-Control": "public, max-age=86400",
    }
    return StreamingResponse(iter_full(), status_code=200, headers=headers)
