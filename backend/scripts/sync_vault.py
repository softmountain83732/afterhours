"""
Sync Vault Script for Afterhours.

Fetches existing audio messages from the private Telegram Vault channel (TELEGRAM_VAULT_CHAT_ID),
extracts audio metadata and embedded artwork using Mutagen & Pillow,
deduplicates via SHA-256 hashes, and records tracks and track_artwork
into Supabase PostgreSQL and local cache.
"""

import argparse
import asyncio
import logging
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

# Ensure backend root is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.config import get_settings
from app.db import db
from app.services.ingestion import IngestionService
from app.services.supabase_client import supabase_service
from app.services.telegram import telegram_service
from app.services.vault_listener import vault_listener_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("sync_vault")


class VaultSynchronizer:
    """Orchestrates syncing audio from Telegram Vault into Supabase & DB store."""

    def __init__(
        self,
        chat_id: Optional[int] = None,
        limit: int = 100,
        offset_id: int = 0,
        batch_size: int = 20,
        dry_run: bool = False
    ):
        self.settings = get_settings()
        self.chat_id = chat_id or self.settings.TELEGRAM_VAULT_CHAT_ID
        self.limit = limit
        # Default to latest persistent checkpoint if offset_id not specified
        if offset_id == 0:
            self.offset_id = vault_listener_service.get_checkpoint(self.chat_id)
        else:
            self.offset_id = offset_id
        self.batch_size = batch_size
        self.dry_run = dry_run

        self.admin_supabase = supabase_service.get_admin()
        self.is_live_supabase = supabase_service.is_live

        # Stats
        self.scanned = 0
        self.ingested = 0
        self.skipped_duplicates = 0
        self.artworks_extracted = 0
        self.errors = 0

    async def run(self):
        """Execute the sync process."""
        logger.info("==================================================")
        logger.info("Starting Afterhours Vault Synchronization")
        logger.info(f"Target Vault Chat ID: {self.chat_id}")
        logger.info(f"Scan Limit: {self.limit} | Offset ID: {self.offset_id} | Dry Run: {self.dry_run}")
        logger.info(f"Live Supabase Connected: {self.is_live_supabase}")
        logger.info("==================================================")

        # 1. Initialize Telegram MTProto client
        await telegram_service.initialize()

        if not telegram_service.is_connected:
            logger.warning(
                "Telethon client is not connected (placeholder or missing credentials). "
                "Running in simulated verification mode."
            )
            await self._run_simulated_sync()
            return

        try:
            logger.info("Scanning Telegram Vault channel for audio messages...")
            current_batch = []

            async for message in telegram_service.iter_audio_messages(
                chat_id=self.chat_id,
                limit=self.limit,
                offset_id=self.offset_id
            ):
                self.scanned += 1
                try:
                    success = await self._process_message(message)
                    if success:
                        current_batch.append(message.id)

                    if len(current_batch) >= self.batch_size:
                        logger.info(f"Batch checkpoint reached ({len(current_batch)} tracks processed).")
                        current_batch.clear()

                except Exception as e:
                    self.errors += 1
                    logger.error(f"Error processing message {message.id}: {e}", exc_info=True)

        finally:
            await telegram_service.disconnect()

        self._print_summary()

    async def _process_message(self, message: Any) -> bool:
        """Process a single Telegram audio message."""
        msg_id = message.id
        logger.info(f"[{self.scanned}/{self.limit}] Inspecting Message ID {msg_id}...")

        # 1. Check if message reference already exists in database
        if self._is_already_ingested(self.chat_id, msg_id):
            logger.info(f"Message ID {msg_id} already ingested. Skipping.")
            self.skipped_duplicates += 1
            return False

        # 2. Determine file suffix
        file_name = getattr(message, "file", None)
        raw_name = getattr(file_name, "name", "") if file_name else ""
        suffix = Path(raw_name).suffix.lower() if raw_name else ".flac"
        if not suffix:
            suffix = ".flac"

        # 3. Download audio file to temporary location
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_audio:
            temp_path = Path(temp_audio.name)

        try:
            downloaded_path = await message.download_media(file=str(temp_path))
            if not downloaded_path or not Path(downloaded_path).exists():
                logger.warning(f"Failed to download media for message {msg_id}. Skipping.")
                self.errors += 1
                return False

            actual_path = Path(downloaded_path)

            # 4. Compute file hash for duplicate detection
            file_hash = IngestionService.compute_file_hash(actual_path)
            if self._is_hash_exists(file_hash):
                logger.info(f"Exact file duplicate detected (hash {file_hash[:12]}...). Skipping re-ingestion.")
                self.skipped_duplicates += 1
                return False

            # 5. Extract metadata with Mutagen
            metadata = IngestionService.extract_metadata(actual_path)
            title = metadata.get("title") or Path(raw_name).stem or f"Track {msg_id}"
            artist_name = metadata.get("artist") or "Unknown Artist"
            album_name = metadata.get("album") or "Unknown Album"
            duration_ms = metadata.get("duration_ms", 0)
            track_number = metadata.get("track_number")
            codec = metadata.get("codec", suffix.lstrip("."))
            bitrate = metadata.get("bitrate")
            file_size = actual_path.stat().st_size

            # 6. Extract embedded artwork and optimize
            artwork_bytes = IngestionService.extract_artwork(actual_path)
            artwork_id = None
            artwork_url = None
            artwork_thumb_url = None
            img_hash = None

            if artwork_bytes:
                try:
                    full_webp, thumb_webp = IngestionService.optimize_artwork(artwork_bytes)
                    img_hash = IngestionService.compute_image_hash(artwork_bytes)

                    # Save to local artwork cache
                    art_dir = Path(self.settings.ARTWORK_CACHE_DIR)
                    art_dir.mkdir(parents=True, exist_ok=True)
                    full_path = art_dir / f"{img_hash}_full.webp"
                    thumb_path = art_dir / f"{img_hash}_thumb.webp"

                    with open(full_path, "wb") as f:
                        f.write(full_webp)
                    with open(thumb_path, "wb") as f:
                        f.write(thumb_webp)

                    artwork_url = f"/api/static/artwork/{img_hash}_full.webp"
                    artwork_thumb_url = f"/api/static/artwork/{img_hash}_thumb.webp"
                    self.artworks_extracted += 1
                except Exception as e:
                    logger.warning(f"Artwork extraction/optimization failed for msg {msg_id}: {e}")

            if self.dry_run:
                logger.info(f"[DRY-RUN] Extracted: '{title}' by '{artist_name}' ({codec}, {duration_ms}ms)")
                return True

            # 7. Upsert Artist & Album
            artist_id = await self._get_or_create_artist(artist_name)
            album_id = await self._get_or_create_album(album_name, artist_id, artwork_url, artwork_thumb_url)

            # 8. Insert into tracks table
            track_id = str(uuid.uuid4())
            track_data = {
                "id": track_id,
                "title": title,
                "artist_id": artist_id,
                "album_id": album_id,
                "duration_ms": duration_ms,
                "track_number": track_number,
                "codec": codec,
                "bitrate": bitrate,
                "file_hash": file_hash,
                "telegram_chat_id": self.chat_id,
                "telegram_message_id": msg_id,
                "uploaded_by": None,  # Vault tracks are system/owner library tracks
                "is_private": False,
                "file_size": file_size,
                "metadata_raw": metadata,
                "ingestion_status": "completed",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            # Insert into Supabase if live
            if self.is_live_supabase and self.admin_supabase:
                try:
                    self.admin_supabase.table("tracks").insert(track_data).execute()
                except Exception as e:
                    logger.error(f"Supabase tracks insert error: {e}")

            # Also update local db store
            db.tracks[track_id] = {
                **track_data,
                "artist_name": artist_name,
                "album_title": album_name,
                "artwork_url": artwork_url,
                "artwork_thumb_url": artwork_thumb_url,
            }

            # 9. Insert into track_artwork table if artwork exists
            if img_hash and artwork_url:
                artwork_id = str(uuid.uuid4())
                artwork_data = {
                    "id": artwork_id,
                    "track_id": track_id,
                    "image_hash": img_hash,
                    "original_url": artwork_url,
                    "thumb_url": artwork_thumb_url,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }

                if self.is_live_supabase and self.admin_supabase:
                    try:
                        self.admin_supabase.table("track_artwork").insert(artwork_data).execute()
                    except Exception as e:
                        logger.error(f"Supabase track_artwork insert error: {e}")

                db.track_artwork[artwork_id] = artwork_data

            self.ingested += 1
            vault_listener_service.save_checkpoint(self.chat_id, msg_id)
            logger.info(f"✓ Ingested Track [{track_id[:8]}]: '{title}' by {artist_name} ({codec.upper()})")
            return True

        finally:
            temp_path.unlink(missing_ok=True)

    def _is_already_ingested(self, chat_id: int, message_id: int) -> bool:
        """Check if message is already recorded in tracks table."""
        # Check in-memory store
        for t in db.tracks.values():
            if t.get("telegram_chat_id") == chat_id and t.get("telegram_message_id") == message_id:
                return True

        # Check live Supabase
        if self.is_live_supabase and self.admin_supabase:
            try:
                res = self.admin_supabase.table("tracks").select("id").eq("telegram_chat_id", chat_id).eq("telegram_message_id", message_id).execute()
                if res.data and len(res.data) > 0:
                    return True
            except Exception:
                pass
        return False

    def _is_hash_exists(self, file_hash: str) -> bool:
        """Check if file hash already exists."""
        for t in db.tracks.values():
            if t.get("file_hash") == file_hash:
                return True

        if self.is_live_supabase and self.admin_supabase:
            try:
                res = self.admin_supabase.table("tracks").select("id").eq("file_hash", file_hash).execute()
                if res.data and len(res.data) > 0:
                    return True
            except Exception:
                pass
        return False

    async def _get_or_create_artist(self, artist_name: str) -> str:
        """Find or create artist record."""
        # In-memory check
        for a in db.artists.values():
            if a.get("name", "").lower() == artist_name.lower():
                return a["id"]

        # Supabase check
        if self.is_live_supabase and self.admin_supabase:
            try:
                res = self.admin_supabase.table("artists").select("id").ilike("name", artist_name).execute()
                if res.data and len(res.data) > 0:
                    return res.data[0]["id"]
            except Exception:
                pass

        artist_id = str(uuid.uuid4())
        artist_data = {
            "id": artist_id,
            "name": artist_name,
            "image_url": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if self.is_live_supabase and self.admin_supabase:
            try:
                self.admin_supabase.table("artists").insert(artist_data).execute()
            except Exception:
                pass

        db.artists[artist_id] = artist_data
        return artist_id

    async def _get_or_create_album(
        self,
        album_name: str,
        artist_id: str,
        artwork_url: Optional[str] = None,
        artwork_thumb_url: Optional[str] = None
    ) -> str:
        """Find or create album record."""
        # In-memory check
        for al in db.albums.values():
            if al.get("title", "").lower() == album_name.lower() and al.get("artist_id") == artist_id:
                return al["id"]

        # Supabase check
        if self.is_live_supabase and self.admin_supabase:
            try:
                res = self.admin_supabase.table("albums").select("id").ilike("title", album_name).eq("artist_id", artist_id).execute()
                if res.data and len(res.data) > 0:
                    return res.data[0]["id"]
            except Exception:
                pass

        album_id = str(uuid.uuid4())
        album_data = {
            "id": album_id,
            "title": album_name,
            "artist_id": artist_id,
            "artwork_url": artwork_url,
            "artwork_thumb_url": artwork_thumb_url,
            "release_year": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if self.is_live_supabase and self.admin_supabase:
            try:
                self.admin_supabase.table("albums").insert(album_data).execute()
            except Exception:
                pass

        db.albums[album_id] = album_data
        return album_id

    async def _run_simulated_sync(self):
        """
        Simulated sync for local verification when Telegram credentials are not yet configured.
        Demonstrates complete ingestion pipeline, metadata parsing, hashing, artwork optimization,
        and database insertion.
        """
        logger.info("Running sample Vault audio ingestion test...")
        import subprocess

        # Generate sample audio file using FFmpeg
        test_flac = Path("temp_sync_vault_sample.flac")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "sine=frequency=432:duration=10",
                "-metadata", "title=Midnight Vault Echoes",
                "-metadata", "artist=Afterhours Resonance",
                "-metadata", "album=Vault Archives Vol. 1",
                "-c:a", "flac",
                str(test_flac)
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )

        try:
            self.scanned = 1
            file_hash = IngestionService.compute_file_hash(test_flac)
            metadata = IngestionService.extract_metadata(test_flac)

            artist_id = await self._get_or_create_artist(metadata["artist"])
            album_id = await self._get_or_create_album(metadata["album"], artist_id)

            track_id = str(uuid.uuid4())
            track_data = {
                "id": track_id,
                "title": metadata["title"],
                "artist_id": artist_id,
                "album_id": album_id,
                "duration_ms": metadata["duration_ms"],
                "track_number": 1,
                "codec": metadata["codec"],
                "bitrate": metadata["bitrate"],
                "file_hash": file_hash,
                "telegram_chat_id": self.chat_id,
                "telegram_message_id": 1001,
                "uploaded_by": None,
                "is_private": False,
                "file_size": test_flac.stat().st_size,
                "metadata_raw": metadata,
                "ingestion_status": "completed",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            db.tracks[track_id] = {
                **track_data,
                "artist_name": metadata["artist"],
                "album_title": metadata["album"],
            }

            self.ingested += 1
            logger.info(f"✓ Ingested simulated Vault track: '{metadata['title']}' ({track_id})")

        finally:
            test_flac.unlink(missing_ok=True)

        self._print_summary()

    def _print_summary(self):
        """Print execution summary."""
        logger.info("==================================================")
        logger.info("Sync Vault Execution Summary")
        logger.info("==================================================")
        logger.info(f"Messages Scanned:       {self.scanned}")
        logger.info(f"Tracks Newly Ingested:  {self.ingested}")
        logger.info(f"Duplicates Skipped:     {self.skipped_duplicates}")
        logger.info(f"Artworks Extracted:     {self.artworks_extracted}")
        logger.info(f"Errors Encountered:     {self.errors}")
        logger.info("==================================================")


def main():
    parser = argparse.ArgumentParser(
        description="Sync audio messages from Telegram Afterhours Vault into Supabase"
    )
    parser.add_argument("--limit", type=int, default=100, help="Maximum messages to scan (default: 100)")
    parser.add_argument("--offset-id", type=int, default=0, help="Telegram message ID offset to resume from")
    parser.add_argument("--batch-size", type=int, default=20, help="Batch size for progress checkpoints")
    parser.add_argument("--vault-chat-id", type=int, default=None, help="Override TELEGRAM_VAULT_CHAT_ID")
    parser.add_argument("--dry-run", action="store_true", help="Parse and inspect without writing to database")

    args = parser.parse_args()

    sync = VaultSynchronizer(
        chat_id=args.vault_chat_id,
        limit=args.limit,
        offset_id=args.offset_id,
        batch_size=args.batch_size,
        dry_run=args.dry_run
    )
    asyncio.run(sync.run())


if __name__ == "__main__":
    main()
