"""Pre-cache service and CLI script for downloading and transcoding Telegram Vault tracks."""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

# Ensure backend package can be imported
current_dir = Path(__file__).resolve().parent
backend_root = current_dir.parent.parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

from app.config import get_settings
from app.services.audio import AudioService
from app.services.cache import FileCache
from app.services.telegram import telegram_service
from app.services.supabase_client import supabase_service
from app.db import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("precache_service")


async def precache_tracks(
    quality: str = "standard",
    track_id: Optional[str] = None,
    force: bool = False
) -> Dict[str, Any]:
    """
    Download and transcode all completed tracks from Telegram Vault into the local cache.
    Returns summary report of precached tracks.
    """
    settings = get_settings()
    audio_cache = FileCache(settings.AUDIO_CACHE_DIR, settings.MAX_CACHE_SIZE_MB)
    bitrate = AudioService.get_quality_bitrate(quality)

    print(f"\n=======================================================")
    print(f" AFTERHOURS AUDIO PRE-CACHE SERVICE")
    print(f" Cache Directory: {settings.AUDIO_CACHE_DIR}")
    print(f" Target Quality:  {quality} ({bitrate})")
    print(f" Force Refresh:   {force}")
    print(f"=======================================================\n")

    # 1. Fetch completed tracks from Supabase or memory
    tracks = []
    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        query = admin.table("tracks").select("*").eq("ingestion_status", "completed")
        if track_id:
            query = query.eq("id", track_id)
        res = query.order("created_at").execute()
        tracks = res.data or []
        print(f"Fetched {len(tracks)} completed tracks from live Supabase.")
    else:
        print("Supabase offline; checking in-memory tracks.")
        for t in db.tracks.values():
            if t.get("ingestion_status") == "completed":
                if not track_id or t.get("id") == track_id:
                    tracks.append(t)

    # Filter tracks with valid Telegram message ID
    valid_tracks = [t for t in tracks if t.get("telegram_message_id")]
    print(f"Identified {len(valid_tracks)} tracks with valid Telegram message references.\n")

    if not valid_tracks:
        print("No eligible tracks found for pre-caching.")
        return {"total": 0, "cached": 0, "skipped": 0, "failed": 0, "results": []}

    # 2. Ensure Telegram client is initialized
    print("Connecting to Telegram Vault...")
    await telegram_service.initialize()
    if not telegram_service.is_connected:
        print("ERROR: Failed to connect and authorize Telegram client.")
        return {"total": len(valid_tracks), "cached": 0, "skipped": 0, "failed": len(valid_tracks), "error": "Telegram unavailable"}

    results = []
    cached_count = 0
    skipped_count = 0
    failed_count = 0

    # 3. Process each track sequentially to prevent network/CPU saturation
    for idx, track in enumerate(valid_tracks, 1):
        t_id = track["id"]
        title = track.get("title", "Untitled")
        chat_id = track.get("telegram_chat_id") or settings.TELEGRAM_VAULT_CHAT_ID
        msg_id = track.get("telegram_message_id")
        codec = track.get("codec") or "flac"

        print(f"[{idx}/{len(valid_tracks)}] Processing: '{title}' (ID: {t_id}, Msg: {msg_id})")

        cache_key = f"{t_id}_{quality}"
        dest_path = audio_cache.get_path(cache_key, "m4a")
        raw_cache_key = f"{t_id}_raw"
        raw_path = audio_cache.get_path(raw_cache_key, codec)

        # Check existing cached M4A
        if not force and dest_path.exists() and AudioService.validate_audio_file(dest_path):
            file_size = dest_path.stat().st_size
            print(f"    -> [SKIP] Already cached and validated ({file_size} bytes)")
            skipped_count += 1
            results.append({
                "id": t_id,
                "title": title,
                "message_id": msg_id,
                "status": "skipped",
                "m4a_size": file_size,
                "validated": True
            })
            continue

        # If corrupt file exists, purge it
        if dest_path.exists():
            dest_path.unlink(missing_ok=True)

        # Check / download raw source from Vault
        raw_downloaded = False
        if not force and raw_path.exists() and AudioService.validate_audio_file(raw_path):
            raw_size = raw_path.stat().st_size
            print(f"    -> Raw source already cached ({raw_size} bytes)")
            raw_downloaded = True
        else:
            if raw_path.exists():
                raw_path.unlink(missing_ok=True)
            print(f"    -> Downloading from Telegram Vault (chat {chat_id}, msg {msg_id})...")
            last_pct = [-10]
            def progress(current, total):
                if total:
                    pct = int(current * 100 / total)
                    if pct >= last_pct[0] + 10 or current == total:
                        last_pct[0] = (pct // 10) * 10
                        print(f"       -> Progress: {current}/{total} ({pct}%)")

            try:
                dl_result = await telegram_service.download_file_from_vault(
                    chat_id,
                    msg_id,
                    raw_path,
                    progress_callback=progress
                )
                if dl_result and raw_path.exists() and AudioService.validate_audio_file(raw_path):
                    raw_size = raw_path.stat().st_size
                    print(f"    -> Download complete & validated ({raw_size} bytes)")
                    raw_downloaded = True
                else:
                    if raw_path.exists():
                        raw_path.unlink(missing_ok=True)
                    print(f"    -> [FAIL] Downloaded file is empty or corrupt")
            except Exception as e:
                print(f"    -> [FAIL] Download exception: {type(e).__name__}: {e}")

        if not raw_downloaded:
            failed_count += 1
            results.append({
                "id": t_id,
                "title": title,
                "message_id": msg_id,
                "status": "download_failed",
                "validated": False
            })
            continue

        # Transcode to AAC M4A
        print(f"    -> Transcoding to AAC M4A ({bitrate})...")
        try:
            success = await AudioService.transcode_to_aac(raw_path, dest_path, bitrate)
            if success and dest_path.exists() and AudioService.validate_audio_file(dest_path):
                m4a_size = dest_path.stat().st_size
                print(f"    -> [SUCCESS] Transcode verified: {m4a_size} bytes (AAC M4A)")
                cached_count += 1
                results.append({
                    "id": t_id,
                    "title": title,
                    "message_id": msg_id,
                    "status": "cached",
                    "original_size": raw_path.stat().st_size,
                    "m4a_size": m4a_size,
                    "validated": True
                })
            else:
                if dest_path.exists():
                    dest_path.unlink(missing_ok=True)
                print(f"    -> [FAIL] Transcoding failed validation")
                failed_count += 1
                results.append({
                    "id": t_id,
                    "title": title,
                    "message_id": msg_id,
                    "status": "transcode_failed",
                    "validated": False
                })
        except Exception as e:
            print(f"    -> [FAIL] Transcode exception: {type(e).__name__}: {e}")
            failed_count += 1
            results.append({
                "id": t_id,
                "title": title,
                "message_id": msg_id,
                "status": "transcode_error",
                "validated": False
            })

    print(f"\n=======================================================")
    print(f" PRE-CACHE COMPLETE")
    print(f" Total Eligible: {len(valid_tracks)}")
    print(f" Newly Cached:   {cached_count}")
    print(f" Already Cached: {skipped_count}")
    print(f" Failed:         {failed_count}")
    print(f"=======================================================\n")

    return {
        "total": len(valid_tracks),
        "cached": cached_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "results": results
    }


def main():
    parser = argparse.ArgumentParser(description="Afterhours Audio Pre-cache Tool")
    parser.add_argument("--quality", default="standard", choices=["standard", "high", "saver"], help="Audio quality bitrate")
    parser.add_argument("--track-id", default=None, help="Specific track ID to pre-cache")
    parser.add_argument("--force", action="store_true", help="Force re-download and re-transcode")
    args = parser.parse_args()

    async def run():
        try:
            await precache_tracks(quality=args.quality, track_id=args.track_id, force=args.force)
        finally:
            await telegram_service.disconnect()

    asyncio.run(run())


if __name__ == "__main__":
    main()
