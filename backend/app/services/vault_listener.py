"""Telegram Vault real-time listener, offline recovery reconciler, and automated ingestion pipeline."""

import asyncio
import json
import logging
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

from telethon import events
from telethon.tl.types import MessageMediaDocument, DocumentAttributeAudio

from app.config import get_settings
from app.db import db
from app.services.audio import AudioService
from app.services.ingestion import IngestionService
from app.services.supabase_client import supabase_service
from app.services.telegram import telegram_service
from app.services.track_cache import track_response_cache

logger = logging.getLogger("vault_listener")

SUPPORTED_AUDIO_EXTENSIONS = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav"}
DEFAULT_FALLBACK_ARTWORK_URL = "/api/static/artwork/default.svg"
DEFAULT_FALLBACK_ARTWORK_THUMB_URL = "/api/static/artwork/default.svg"


class WatermarkTracker:
    """
    Thread-safe tracker guaranteeing strictly contiguous checkpoint advancement per chat.
    Prevents newer messages from jumping the persistent checkpoint past older pending or failed messages.
    """

    def __init__(self, chat_id: int, initial_checkpoint: int):
        self.chat_id = chat_id
        self.checkpoint = initial_checkpoint
        self.processed_ids: set[int] = set()
        self.failed_ids: set[int] = set()
        self.lock = asyncio.Lock()

    def set_checkpoint(self, checkpoint: int):
        """Directly update baseline checkpoint (e.g. from external sync or explicit save)."""
        self.checkpoint = max(self.checkpoint, checkpoint)
        self.processed_ids = {mid for mid in self.processed_ids if mid > self.checkpoint}
        self.failed_ids = {mid for mid in self.failed_ids if mid > self.checkpoint}

    async def mark_processed(self, message_id: int) -> Optional[int]:
        """
        Record a message as successfully processed (or verified non-audio/unsupported/duplicate).
        Advances the checkpoint contiguously as far as possible.
        Returns the new checkpoint if it advanced, or None.
        """
        async with self.lock:
            if message_id <= self.checkpoint:
                return None

            self.processed_ids.add(message_id)
            self.failed_ids.discard(message_id)

            # Contiguously advance checkpoint
            advanced = False
            new_cp = self.checkpoint
            while (new_cp + 1) in self.processed_ids:
                new_cp += 1
                self.processed_ids.remove(new_cp)
                advanced = True

            if advanced:
                self.checkpoint = new_cp
                return new_cp
            return None

    async def mark_failed(self, message_id: int):
        """
        Record a message as failed (download error, corrupt media, DB error).
        Halts checkpoint advancement at or before this message until it succeeds.
        """
        async with self.lock:
            if message_id > self.checkpoint:
                self.failed_ids.add(message_id)
                self.processed_ids.discard(message_id)


class VaultListenerService:
    """Manages real-time Telegram Vault event listening, offline reconciliation, and automated ingestion."""

    def __init__(self):
        self.settings = get_settings()
        self.chat_id = int(self.settings.TELEGRAM_VAULT_CHAT_ID)
        self.is_running = False
        self._handler_registered = False
        self._startup_task: Optional[asyncio.Task] = None
        self._reconcile_task: Optional[asyncio.Task] = None
        self._queue_worker_task: Optional[asyncio.Task] = None
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._trackers: Dict[int, WatermarkTracker] = {}
        self._ingestion_locks: Dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()
        self.checkpoint_file = Path(self.settings.AUDIO_CACHE_DIR).parent / "vault_checkpoints.json"

    def _get_tracker(self, chat_id: int) -> WatermarkTracker:
        """Get or initialize the thread-safe watermark tracker for a channel."""
        if chat_id not in self._trackers:
            cp = self.get_checkpoint(chat_id)
            self._trackers[chat_id] = WatermarkTracker(chat_id, cp)
        return self._trackers[chat_id]

    def is_audio_message(self, message: Any) -> bool:
        """
        Validate whether a Telegram message is a supported audio document.
        Examines document attributes, MIME types, and file extensions.
        Ignores text, photos, stickers, and unrelated media.
        """
        if not message or not getattr(message, "media", None):
            return False

        if not isinstance(message.media, MessageMediaDocument):
            return False

        document = getattr(message.media, "document", None)
        if not document:
            return False

        # 1. Check for audio document attribute
        has_audio_attr = any(isinstance(attr, DocumentAttributeAudio) for attr in getattr(document, "attributes", []))
        mime = getattr(document, "mime_type", "") or ""
        mime_lower = mime.lower()

        # 2. Check MIME type
        is_audio_mime = mime_lower.startswith("audio/") or mime_lower in (
            "application/ogg",
            "application/x-flac",
            "application/flac",
        )

        # 3. Check filename extension
        file_name = getattr(message, "file", None)
        name = getattr(file_name, "name", "") if file_name else ""
        ext = Path(name).suffix.lower() if name else ""
        has_valid_ext = ext in SUPPORTED_AUDIO_EXTENSIONS

        return has_audio_attr or is_audio_mime or has_valid_ext

    async def _get_ingestion_lock(self, chat_id: int, message_id: int) -> asyncio.Lock:
        """Ensure duplicate concurrent events for the same message are serialized safely."""
        key = f"{chat_id}:{message_id}"
        async with self._registry_lock:
            if key not in self._ingestion_locks:
                self._ingestion_locks[key] = asyncio.Lock()
            return self._ingestion_locks[key]

    # =========================================================================
    # CHECKPOINT PERSISTENCE
    # =========================================================================

    def get_checkpoint(self, chat_id: Optional[int] = None) -> int:
        """
        Retrieve latest scanned message ID checkpoint for a Vault channel.
        Checks active tracker, Supabase, local JSON file, and memory store.
        """
        cid = chat_id or self.chat_id

        if cid in self._trackers:
            return self._trackers[cid].checkpoint

        # 1. Check in-memory store
        mem_cp = getattr(db, "vault_sync_checkpoints", {}).get(cid)
        if mem_cp:
            return int(mem_cp)

        # 2. Check Supabase
        if supabase_service.is_live:
            admin = supabase_service.get_admin()
            if admin:
                try:
                    res = admin.table("vault_sync_checkpoints").select("last_scanned_message_id").eq("chat_id", cid).execute()
                    if res.data and len(res.data) > 0:
                        cp = int(res.data[0]["last_scanned_message_id"])
                        self._save_local_checkpoint(cid, cp)
                        return cp
                except Exception:
                    pass

        # 3. Check local JSON file
        return self._load_local_checkpoint(cid)

    def save_checkpoint(self, chat_id: int, message_id: int):
        """
        Persist latest scanned message ID checkpoint.
        Updates watermark tracker, writes to Supabase (if available), local persistent file, and memory.
        """
        # Synchronize active tracker if initialized
        if chat_id in self._trackers:
            self._trackers[chat_id].set_checkpoint(message_id)

        # Memory
        if not hasattr(db, "vault_sync_checkpoints"):
            db.vault_sync_checkpoints = {}
        db.vault_sync_checkpoints[chat_id] = max(
            db.vault_sync_checkpoints.get(chat_id, 0),
            message_id
        )

        # Local file
        self._save_local_checkpoint(chat_id, message_id)

        # Supabase
        if supabase_service.is_live:
            admin = supabase_service.get_admin()
            if admin:
                try:
                    admin.table("vault_sync_checkpoints").upsert({
                        "chat_id": chat_id,
                        "last_scanned_message_id": message_id,
                        "last_sync_at": datetime.now(timezone.utc).isoformat(),
                    }).execute()
                except Exception as e:
                    logger.debug(f"Failed to upsert Supabase checkpoint (table may not be migrated yet): {e}")

    def _load_local_checkpoint(self, chat_id: int) -> int:
        """Read checkpoint from local persistent file."""
        if self.checkpoint_file.exists():
            try:
                data = json.loads(self.checkpoint_file.read_text(encoding="utf-8"))
                return int(data.get(str(chat_id), 0))
            except Exception:
                pass
        return 0

    def _save_local_checkpoint(self, chat_id: int, message_id: int):
        """Save checkpoint to local persistent file."""
        try:
            self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if self.checkpoint_file.exists():
                try:
                    data = json.loads(self.checkpoint_file.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            current_max = int(data.get(str(chat_id), 0))
            data[str(chat_id)] = max(current_max, message_id)
            self.checkpoint_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Failed to write local checkpoint file: {e}")

    # =========================================================================
    # INGESTION PIPELINE
    # =========================================================================

    def _is_already_ingested(self, chat_id: int, message_id: int) -> bool:
        """Check if message is already recorded in tracks table."""
        # Check in-memory store
        for t in db.tracks.values():
            if t.get("telegram_chat_id") == chat_id and t.get("telegram_message_id") == message_id:
                return True

        # Check live Supabase
        if supabase_service.is_live:
            admin = supabase_service.get_admin()
            if admin:
                try:
                    res = admin.table("tracks").select("id").eq("telegram_chat_id", chat_id).eq("telegram_message_id", message_id).execute()
                    if res.data and len(res.data) > 0:
                        return True
                except Exception:
                    pass
        return False

    def _is_hash_exists(self, file_hash: str) -> bool:
        """Check if audio file hash already exists."""
        for t in db.tracks.values():
            if t.get("file_hash") == file_hash:
                return True

        if supabase_service.is_live:
            admin = supabase_service.get_admin()
            if admin:
                try:
                    res = admin.table("tracks").select("id").eq("file_hash", file_hash).execute()
                    if res.data and len(res.data) > 0:
                        return True
                except Exception:
                    pass
        return False

    async def _get_or_create_artist(self, artist_name: str) -> str:
        """Find or create artist record."""
        clean_name = artist_name.strip() or "Unknown Artist"
        for a in db.artists.values():
            if a.get("name", "").lower() == clean_name.lower():
                return a["id"]

        if supabase_service.is_live:
            admin = supabase_service.get_admin()
            if admin:
                try:
                    res = admin.table("artists").select("id").ilike("name", clean_name).execute()
                    if res.data and len(res.data) > 0:
                        return res.data[0]["id"]
                except Exception:
                    pass

        artist_id = str(uuid.uuid4())
        artist_data = {
            "id": artist_id,
            "name": clean_name,
            "image_url": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if supabase_service.is_live:
            admin = supabase_service.get_admin()
            if admin:
                try:
                    admin.table("artists").insert(artist_data).execute()
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
        clean_title = album_name.strip() or "Unknown Album"
        for al in db.albums.values():
            if al.get("title", "").lower() == clean_title.lower() and al.get("artist_id") == artist_id:
                return al["id"]

        if supabase_service.is_live:
            admin = supabase_service.get_admin()
            if admin:
                try:
                    res = admin.table("albums").select("id").ilike("title", clean_title).eq("artist_id", artist_id).execute()
                    if res.data and len(res.data) > 0:
                        return res.data[0]["id"]
                except Exception:
                    pass

        album_id = str(uuid.uuid4())
        album_data = {
            "id": album_id,
            "title": clean_title,
            "artist_id": artist_id,
            "artwork_url": artwork_url,
            "artwork_thumb_url": artwork_thumb_url,
            "release_year": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if supabase_service.is_live:
            admin = supabase_service.get_admin()
            if admin:
                try:
                    admin.table("albums").insert(album_data).execute()
                except Exception:
                    pass

        db.albums[album_id] = album_data
        return album_id

    async def ingest_message(self, message: Any) -> Optional[Dict[str, Any]]:
        """
        Process a single Vault audio message:
        - Deduplication check (chat_id + msg_id, file_hash)
        - Temp download to extract metadata & embedded artwork
        - Upsert artist & album
        - Insert track & artwork into Supabase & db
        - Immediate cleanup of temporary raw download
        Returns created track dict on success, None on skip/failure.
        """
        if not self.is_audio_message(message):
            return None

        msg_id = getattr(message, "id", None)
        chat_id = getattr(message, "chat_id", None) or self.chat_id
        if not msg_id:
            return None

        lock = await self._get_ingestion_lock(chat_id, msg_id)
        async with lock:
            if self._is_already_ingested(chat_id, msg_id):
                logger.info(f"Message ID {msg_id} already ingested; skipping.")
                return None

            file_name = getattr(message, "file", None)
            raw_name = getattr(file_name, "name", "") if file_name else ""
            suffix = Path(raw_name).suffix.lower() if raw_name else ".flac"
            if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
                suffix = ".flac"

            # 1. Download to temporary file for metadata & artwork extraction
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_audio:
                temp_path = Path(temp_audio.name)

            try:
                # Use message's own download_media or telegram_service with timeout protection
                downloaded_path = None
                try:
                    downloaded_path = await asyncio.wait_for(
                        message.download_media(file=str(temp_path)),
                        timeout=180.0
                    )
                except Exception:
                    downloaded_path = await telegram_service.download_file_from_vault(chat_id, msg_id, temp_path)

                if not downloaded_path or not Path(downloaded_path).exists():
                    logger.warning(f"Failed to download Vault media for message {msg_id}; skipping ingestion.")
                    return None

                actual_path = Path(downloaded_path)

                # 2. Validate media
                if not AudioService.validate_audio_file(actual_path):
                    logger.warning(f"Downloaded media for message {msg_id} failed audio validation; skipping.")
                    return None

                # 3. Deduplication via SHA-256
                file_hash = IngestionService.compute_file_hash(actual_path)
                if self._is_hash_exists(file_hash):
                    logger.info(f"Exact file duplicate detected (hash {file_hash[:12]}...) for msg {msg_id}; skipping.")
                    return None

                # 4. Extract metadata with Mutagen
                metadata = IngestionService.extract_metadata(actual_path)
                title = metadata.get("title") or Path(raw_name).stem or f"Track {msg_id}"
                artist_name = metadata.get("artist") or "Unknown Artist"
                album_name = metadata.get("album") or "Unknown Album"
                duration_ms = metadata.get("duration_ms", 0)
                track_number = metadata.get("track_number")
                codec = metadata.get("codec", suffix.lstrip("."))
                bitrate = metadata.get("bitrate")
                file_size = actual_path.stat().st_size

                # 5. Extract artwork (embedded -> Telegram thumb -> dark luxury fallback)
                artwork_bytes = IngestionService.extract_artwork(actual_path)
                if not artwork_bytes:
                    try:
                        doc = getattr(getattr(message, "media", None), "document", None)
                        has_thumbs = bool(getattr(doc, "thumbs", None)) or bool(getattr(getattr(message, "file", None), "thumbs", None))
                        if has_thumbs:
                            thumb_bytes = await asyncio.wait_for(
                                message.download_media(file=bytes, thumb=-1),
                                timeout=30.0
                            )
                            if isinstance(thumb_bytes, bytes) and len(thumb_bytes) > 0:
                                artwork_bytes = thumb_bytes
                    except Exception as e:
                        logger.debug(f"Could not extract Telegram thumbnail for msg {msg_id}: {e}")

                artwork_id = None
                artwork_url = None
                artwork_thumb_url = None
                img_hash = None

                if artwork_bytes:
                    try:
                        full_webp, thumb_webp = IngestionService.optimize_artwork(artwork_bytes)
                        img_hash = IngestionService.compute_image_hash(artwork_bytes)

                        # Write to local cache
                        art_dir = Path(self.settings.ARTWORK_CACHE_DIR)
                        art_dir.mkdir(parents=True, exist_ok=True)
                        full_path = art_dir / f"{img_hash}_full.webp"
                        thumb_path = art_dir / f"{img_hash}_thumb.webp"

                        full_path.write_bytes(full_webp)
                        thumb_path.write_bytes(thumb_webp)

                        # Default local URLs
                        artwork_url = f"/api/static/artwork/{img_hash}_full.webp"
                        artwork_thumb_url = f"/api/static/artwork/{img_hash}_thumb.webp"

                        # Persistent Supabase Storage upload
                        if supabase_service.is_live:
                            admin = supabase_service.get_admin()
                            if admin:
                                try:
                                    # Ensure public bucket exists for permanent persistence across dyno restarts
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
                                except Exception as st_err:
                                    logger.warning(f"Supabase Storage artwork upload failed for msg {msg_id}: {st_err}")
                    except Exception as e:
                        logger.warning(f"Artwork extraction/optimization failed for msg {msg_id}: {e}")

                if not artwork_url:
                    artwork_url = DEFAULT_FALLBACK_ARTWORK_URL
                    artwork_thumb_url = DEFAULT_FALLBACK_ARTWORK_THUMB_URL

                # 6. Upsert Artist & Album
                artist_id = await self._get_or_create_artist(artist_name)
                album_id = await self._get_or_create_album(album_name, artist_id, artwork_url, artwork_thumb_url)

                # 7. Insert track
                track_id = str(uuid.uuid4())

                # Preserve downloaded raw audio into cache to accelerate first playback
                try:
                    raw_dest = Path(self.settings.AUDIO_CACHE_DIR) / f"{track_id}_raw.{codec}"
                    if not raw_dest.exists():
                        shutil.copy2(actual_path, raw_dest)
                except Exception as e:
                    logger.debug(f"Could not preserve raw audio cache for msg {msg_id}: {e}")

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
                    "telegram_chat_id": chat_id,
                    "telegram_message_id": msg_id,
                    "uploaded_by": None,  # Vault tracks are public platform tracks
                    "is_private": False,
                    "file_size": file_size,
                    "metadata_raw": metadata,
                    "ingestion_status": "completed",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }

                if supabase_service.is_live:
                    admin = supabase_service.get_admin()
                    if admin:
                        try:
                            admin.table("tracks").insert(track_data).execute()
                        except Exception as e:
                            logger.error(f"Supabase tracks insert error for msg {msg_id}: {e}")

                db.tracks[track_id] = {
                    **track_data,
                    "artist_name": artist_name,
                    "album_title": album_name,
                    "artwork_url": artwork_url,
                    "artwork_thumb_url": artwork_thumb_url,
                }

                # 8. Insert artwork record if present
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

                    if supabase_service.is_live:
                        admin = supabase_service.get_admin()
                        if admin:
                            try:
                                admin.table("track_artwork").insert(artwork_data).execute()
                            except Exception:
                                pass

                    db.track_artwork[artwork_id] = artwork_data

                track_response_cache.invalidate()
                logger.info(f"Auto-ingested Vault message {msg_id}: '{title}' by {artist_name} ({codec.upper()})")
                return track_data

            finally:
                # Clean up temporary raw download immediately
                temp_path.unlink(missing_ok=True)

    # =========================================================================
    # EVENT LISTENER & RECONCILIATION LIFECYCLE
    # =========================================================================

    async def _record_message_outcome(self, chat_id: int, message_id: int, success: bool):
        """
        Record message processing outcome with the watermark tracker.
        Advances persistent checkpoint strictly contiguously when successful.
        Halts checkpoint advancement at or before this message when failed.
        """
        tracker = self._get_tracker(chat_id)
        if success:
            new_cp = await tracker.mark_processed(message_id)
            if new_cp is not None:
                self.save_checkpoint(chat_id, new_cp)
                logger.info(f"Vault checkpoint for chat {chat_id} advanced contiguously to {new_cp}.")
        else:
            await tracker.mark_failed(message_id)
            logger.warning(
                f"Vault message {message_id} failed ingestion; "
                f"checkpoint advancement halted at {tracker.checkpoint}."
            )

    async def _process_message(self, message: Any):
        """
        Process a single incoming or queued Vault message:
        - Filters unsupported media (marking them processed so checkpoint advances)
        - Ingests audio files safely
        - Dispatches outcome to WatermarkTracker
        """
        msg_id = getattr(message, "id", None)
        chat_id = getattr(message, "chat_id", None) or self.chat_id
        if not msg_id:
            return

        if not self.is_audio_message(message):
            # Non-audio message (text, photo, sticker, etc.) is verified unsupported
            await self._record_message_outcome(chat_id, msg_id, success=True)
            return

        logger.info(f"Processing Vault audio message: ID {msg_id} (chat {chat_id})")
        track = await self.ingest_message(message)
        if track is not None:
            await self._record_message_outcome(chat_id, msg_id, success=True)
        elif self._is_already_ingested(chat_id, msg_id):
            # Already in database / duplicate audio hash -> idempotent skip
            await self._record_message_outcome(chat_id, msg_id, success=True)
        else:
            # Genuine failure (download error, corrupt file, validation error, DB error)
            logger.warning(f"Vault audio message {msg_id} failed ingestion; checkpoint will not advance.")
            await self._record_message_outcome(chat_id, msg_id, success=False)

    async def _queue_worker(self):
        """Worker task processing live Vault messages sequentially in arrival order."""
        while self.is_running:
            try:
                message = await self._message_queue.get()
                try:
                    await self._process_message(message)
                finally:
                    self._message_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Vault queue worker: {e}", exc_info=True)

    async def on_vault_message(self, event: Any):
        """Telethon NewMessage event handler for the Vault channel."""
        message = getattr(event, "message", None)
        if not message:
            return

        # If running with background queue worker, enqueue to preserve ordering
        if self.is_running and self._queue_worker_task and not self._queue_worker_task.done():
            await self._message_queue.put(message)
        else:
            # Direct execution (standalone or unit test environment)
            await self._process_message(message)

    async def reconcile_vault(self, chat_id: Optional[int] = None, max_batch: int = 50) -> Dict[str, Any]:
        """
        Incremental reconciliation: scans messages starting from the last checkpoint.
        Recovers missed messages when backend was offline without scanning the entire library.
        Never skips uncompleted or failed messages.
        """
        cid = chat_id or self.chat_id
        tracker = self._get_tracker(cid)
        checkpoint = tracker.checkpoint

        logger.info(f"Starting incremental Vault reconciliation for chat {cid} from checkpoint message {checkpoint}...")

        if not telegram_service.is_connected or not telegram_service.client:
            logger.warning("Telegram client not connected; skipping Vault reconciliation.")
            return {"scanned": 0, "ingested": 0, "checkpoint": checkpoint}

        scanned = 0
        ingested = 0
        latest_seen_id = checkpoint

        try:
            current_id = checkpoint + 1
            batch_size = max_batch
            empty_streak = 0
            max_empty_streak = 2

            while True:
                chunk_ids = list(range(current_id, current_id + batch_size))
                try:
                    messages = await telegram_service.client.get_messages(cid, ids=chunk_ids)
                except Exception as e:
                    logger.error(f"Error fetching messages in range {current_id}..{current_id+batch_size}: {e}")
                    break

                valid_messages = [m for m in messages if m]
                if not valid_messages:
                    empty_streak += 1
                    if empty_streak >= max_empty_streak:
                        break
                else:
                    empty_streak = 0
                    latest_seen_id = max(latest_seen_id, max(m.id for m in valid_messages))

                for idx, expected_id in enumerate(chunk_ids):
                    msg = messages[idx] if idx < len(messages) else None

                    if msg is None:
                        # If this missing ID is before the highest valid ID seen so far,
                        # it represents a deleted/service message in the channel history.
                        if empty_streak == 0 and expected_id < latest_seen_id:
                            await self._record_message_outcome(cid, expected_id, success=True)
                        continue

                    scanned += 1
                    msg_id = msg.id

                    if not self.is_audio_message(msg):
                        # Non-audio message (text, photo, etc.)
                        await self._record_message_outcome(cid, msg_id, success=True)
                        continue

                    result = await self.ingest_message(msg)
                    if result is not None:
                        ingested += 1
                        await self._record_message_outcome(cid, msg_id, success=True)
                    elif self._is_already_ingested(cid, msg_id):
                        await self._record_message_outcome(cid, msg_id, success=True)
                    else:
                        await self._record_message_outcome(cid, msg_id, success=False)

                current_id += batch_size
                if len(valid_messages) < batch_size and empty_streak > 0:
                    break

                await asyncio.sleep(0.05)

            logger.info(
                f"Vault reconciliation complete: {scanned} messages scanned, "
                f"{ingested} ingested. Checkpoint updated to {self.get_checkpoint(cid)}."
            )
            return {"scanned": scanned, "ingested": ingested, "checkpoint": self.get_checkpoint(cid)}

        except Exception as e:
            logger.error(f"Error during Vault reconciliation: {e}")
            return {"scanned": scanned, "ingested": ingested, "error": str(e), "checkpoint": self.get_checkpoint(cid)}

    async def _periodic_reconciliation_loop(self):
        """Background loop executing incremental reconciliation at configured intervals as an ingestion fallback."""
        interval = max(10, min(self.settings.VAULT_RECONCILIATION_INTERVAL_SECONDS, 30))
        while self.is_running:
            try:
                await asyncio.sleep(interval)
                if self.is_running and telegram_service.is_connected:
                    await self.reconcile_vault()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in periodic reconciliation loop: {e}")

    async def start(self):
        """Start the Vault listener, serialized ingestion queue worker, and background reconciliation service."""
        if self.is_running:
            return

        if not self.settings.VAULT_AUTO_INGEST:
            logger.info("Vault auto-ingestion is disabled in settings.")
            return

        if not telegram_service.is_connected or not telegram_service.client:
            logger.warning("Cannot start Vault listener: Telegram client not initialized.")
            return

        self.is_running = True

        # 1. Initialize tracker for vault chat
        self._get_tracker(self.chat_id)

        # 2. Start serialized ingestion queue worker
        self._queue_worker_task = asyncio.create_task(self._queue_worker())

        # 3. Register Telethon event handler only once
        if not self._handler_registered:
            telegram_service.client.add_event_handler(
                self.on_vault_message,
                events.NewMessage(chats=[self.chat_id])
            )
            self._handler_registered = True
            logger.info(f"Registered real-time NewMessage listener for Vault chat {self.chat_id}.")

        # 4. Run initial incremental reconciliation in background (keep strong reference to prevent GC)
        self._startup_task = asyncio.create_task(self.reconcile_vault())

        # 5. Start periodic background reconciliation
        self._reconcile_task = asyncio.create_task(self._periodic_reconciliation_loop())
        logger.info("VaultListenerService started successfully.")

    async def stop(self):
        """Stop the Vault listener and clean up resources without losing queued messages."""
        self.is_running = False

        # 1. Unregister Telegram event handler first
        if self._handler_registered and telegram_service.client:
            try:
                telegram_service.client.remove_event_handler(
                    self.on_vault_message,
                    events.NewMessage(chats=[self.chat_id])
                )
            except Exception as e:
                logger.debug(f"Error removing Telegram event handler: {e}")
            self._handler_registered = False

        # 2. Drain any remaining messages in the queue (with timeout)
        if not self._message_queue.empty():
            q_size = self._message_queue.qsize()
            logger.info(f"Draining {q_size} pending messages in Vault ingestion queue...")
            try:
                await asyncio.wait_for(self._message_queue.join(), timeout=5.0)
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"Queue drain timed out or interrupted ({e}); uncommitted messages remain safe behind checkpoint.")

        # 3. Cancel queue worker task
        if self._queue_worker_task and not self._queue_worker_task.done():
            self._queue_worker_task.cancel()
            try:
                await self._queue_worker_task
            except asyncio.CancelledError:
                pass
            self._queue_worker_task = None

        # 4. Cancel reconciliation tasks
        if self._startup_task and not self._startup_task.done():
            self._startup_task.cancel()
            try:
                await self._startup_task
            except asyncio.CancelledError:
                pass
            self._startup_task = None

        if self._reconcile_task and not self._reconcile_task.done():
            self._reconcile_task.cancel()
            try:
                await self._reconcile_task
            except asyncio.CancelledError:
                pass
            self._reconcile_task = None

        logger.info("VaultListenerService stopped.")


vault_listener_service = VaultListenerService()
