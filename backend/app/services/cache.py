"""LRU File Cache for audio files and artwork with stream validation, SQLite access index, and concurrency locking."""

import asyncio
import logging
import os
import shutil
import sqlite3
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Dict, Any, List, Set

logger = logging.getLogger("cache_service")
AUDIO_EXTENSIONS = {".m4a", ".aac", ".flac", ".mp3", ".ogg", ".wav", ".webm"}


class FileCache:
    """Least-Recently-Used file cache stored on disk with SQLite access tracking and media integrity validation."""

    def __init__(self, cache_dir: str, max_size_mb: int = 5120):
        self.cache_dir = Path(cache_dir)
        self.max_size_bytes = max(1, max_size_mb) * 1024 * 1024
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._locks: Dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()
        self._pinned_counts: Dict[str, int] = defaultdict(int)
        self._pin_lock = asyncio.Lock()

        # SQLite persistent access index location
        self.index_db_path = self.cache_dir.parent / "audio_cache_index.db"
        self._init_sqlite_index()
        self.reconcile_index()

    def _get_db_connection(self) -> sqlite3.Connection:
        """Create a thread-safe connection to the SQLite cache index with WAL mode."""
        conn = sqlite3.connect(str(self.index_db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def _init_sqlite_index(self):
        """Initialize database schema for audio cache access tracking."""
        with self._get_db_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audio_cache_entries (
                    cache_key TEXT PRIMARY KEY,
                    track_id TEXT NOT NULL,
                    quality TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    last_accessed REAL NOT NULL,
                    access_count INTEGER NOT NULL DEFAULT 1,
                    is_pinned INTEGER NOT NULL DEFAULT 0
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_last_accessed 
                ON audio_cache_entries(last_accessed);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_track_id 
                ON audio_cache_entries(track_id);
            """)

    def reconcile_index(self):
        """Reconcile stale index entries with disk and discover valid unindexed files."""
        from app.services.audio import AudioService

        with self._get_db_connection() as conn:
            # 1. Remove entries whose files no longer exist on disk
            cursor = conn.execute("SELECT cache_key, file_path FROM audio_cache_entries")
            rows = cursor.fetchall()
            for row in rows:
                p = Path(row["file_path"])
                if not p.exists() or not p.is_file():
                    conn.execute("DELETE FROM audio_cache_entries WHERE cache_key = ?", (row["cache_key"],))

            # 2. Index any valid cache files on disk missing from the SQLite index
            try:
                for entry in self.cache_dir.iterdir():
                    if entry.is_file() and entry.suffix.lower() in AUDIO_EXTENSIONS:
                        if entry.name.startswith(".tmp_") or ".tmp_" in entry.name:
                            continue

                        # Check for 1028-byte dummy or corrupt legacy file
                        size = entry.stat().st_size
                        if size == 0 or size == 1028 or not AudioService.validate_audio_file(entry):
                            logger.warning(f"Purging corrupt or invalid cache file during reconciliation: {entry.name}")
                            entry.unlink(missing_ok=True)
                            continue

                        # Parse cache_key from file stem (e.g. {track_id}_{quality})
                        stem = entry.stem
                        cur = conn.execute("SELECT cache_key FROM audio_cache_entries WHERE cache_key = ?", (stem,))
                        if not cur.fetchone():
                            # Decompose track_id and quality
                            parts = stem.rsplit("_", 1)
                            if len(parts) == 2 and parts[1] in ("standard", "high", "saver"):
                                t_id, q = parts[0], parts[1]
                            else:
                                t_id, q = stem, "standard"

                            mtime = entry.stat().st_mtime
                            conn.execute("""
                                INSERT OR REPLACE INTO audio_cache_entries
                                (cache_key, track_id, quality, file_path, file_size, created_at, last_accessed, access_count, is_pinned)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (stem, t_id, q, str(entry.resolve()), size, mtime, mtime, 1, 0))
            except Exception as e:
                logger.error(f"Error during cache index disk reconciliation: {e}")

    async def get_lock(self, key: str) -> asyncio.Lock:
        """Get or create an asyncio.Lock for a specific cache key to prevent concurrent work races."""
        async with self._registry_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    def pin(self, key: str):
        """Increment pin count for an active stream or writing file to prevent eviction."""
        self._pinned_counts[key] += 1

    def unpin(self, key: str):
        """Decrement pin count for a completed stream or write."""
        if key in self._pinned_counts:
            self._pinned_counts[key] = max(0, self._pinned_counts[key] - 1)
            if self._pinned_counts[key] == 0:
                del self._pinned_counts[key]

    def is_pinned(self, key: str) -> bool:
        """Check if cache entry is actively pinned."""
        if self._pinned_counts.get(key, 0) > 0:
            return True
        try:
            with self._get_db_connection() as conn:
                cur = conn.execute("SELECT is_pinned FROM audio_cache_entries WHERE cache_key = ?", (key,))
                row = cur.fetchone()
                if row and row["is_pinned"] > 0:
                    return True
        except Exception:
            pass
        return False

    @contextmanager
    def pin_context(self, key: str):
        """Context manager to guarantee cache entry remains pinned during an active streaming response."""
        self.pin(key)
        try:
            yield
        finally:
            self.unpin(key)

    def get_path(self, key: str, extension: str = "") -> Path:
        """Construct canonical file path for a key."""
        clean_key = "".join(c for c in key if c.isalnum() or c in ("-", "_"))
        ext = f".{extension.lstrip('.')}" if extension else ""
        return self.cache_dir / f"{clean_key}{ext}"

    def exists(self, key: str, extension: str = "", validate_media: bool = False) -> bool:
        """Check if file exists in cache and optionally validate audio stream."""
        path = self.get_path(key, extension)
        if not path.exists() or not path.is_file():
            return False

        if validate_media or path.suffix.lower() in AUDIO_EXTENSIONS:
            from app.services.audio import AudioService
            if not AudioService.validate_audio_file(path):
                logger.warning(f"Cache entry {path.name} failed audio validation; rejecting.")
                return False

        self._record_access(key, path)
        return True

    def get(self, key: str, extension: str = "", validate_media: bool = True) -> Optional[Path]:
        """
        Return cached file path and update access timestamp in SQLite index.
        Automatically validates media files and removes corrupt or synthetic placeholders.
        """
        path = self.get_path(key, extension)
        if not path.exists() or not path.is_file():
            return None

        # Check for zero or tiny invalid files
        try:
            size = path.stat().st_size
            if size == 0 or size == 1028:
                logger.warning(f"Removing invalid cache file {path.name} (size: {size} bytes)")
                path.unlink(missing_ok=True)
                self._remove_from_index(key)
                return None
        except OSError:
            return None

        # Audio stream validation for audio files
        if validate_media and path.suffix.lower() in AUDIO_EXTENSIONS:
            from app.services.audio import AudioService
            if not AudioService.validate_audio_file(path):
                logger.warning(f"Cached audio file {path.name} failed media validation; purging.")
                path.unlink(missing_ok=True)
                self._remove_from_index(key)
                return None

        self._record_access(key, path)
        return path

    def _record_access(self, key: str, path: Path):
        """Update last_accessed timestamp and access_count in SQLite and filesystem."""
        now = time.time()
        try:
            os.utime(path, None)
        except OSError:
            pass

        try:
            file_size = path.stat().st_size
            parts = key.rsplit("_", 1)
            t_id = parts[0]
            quality = parts[1] if len(parts) == 2 and parts[1] in ("standard", "high", "saver") else "standard"

            with self._get_db_connection() as conn:
                conn.execute("""
                    INSERT INTO audio_cache_entries
                    (cache_key, track_id, quality, file_path, file_size, created_at, last_accessed, access_count, is_pinned)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        last_accessed = excluded.last_accessed,
                        access_count = audio_cache_entries.access_count + 1,
                        file_size = excluded.file_size
                """, (key, t_id, quality, str(path.resolve()), file_size, now, now))
        except Exception as e:
            logger.debug(f"Failed to record cache access in SQLite for {key}: {e}")

    def _remove_from_index(self, key: str):
        """Delete cache key from SQLite index."""
        try:
            with self._get_db_connection() as conn:
                conn.execute("DELETE FROM audio_cache_entries WHERE cache_key = ?", (key,))
        except Exception as e:
            logger.debug(f"Failed to remove {key} from cache index: {e}")

    def commit(
        self,
        key: str,
        temp_path: Path,
        extension: str = "",
        track_id: str = "",
        quality: str = "standard"
    ) -> Path:
        """
        Commit a completed temporary audio file into the cache atomically.
        Performs media validation, applies LRU eviction if needed, updates the SQLite index.
        """
        dest_path = self.get_path(key, extension)
        file_size = temp_path.stat().st_size

        # Evict old unpinned files if needed before moving into destination
        self.evict_if_needed(additional_bytes=file_size)

        # Pin during file movement
        self.pin(key)
        try:
            if dest_path.exists():
                dest_path.unlink(missing_ok=True)
            temp_path.replace(dest_path)
            now = time.time()
            try:
                os.utime(dest_path, None)
            except OSError:
                pass

            t_id = track_id or key.rsplit("_", 1)[0]
            with self._get_db_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO audio_cache_entries
                    (cache_key, track_id, quality, file_path, file_size, created_at, last_accessed, access_count, is_pinned)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (key, t_id, quality, str(dest_path.resolve()), file_size, now, now, 1, 0))

            return dest_path
        finally:
            self.unpin(key)

    def put(self, key: str, src_path: Path, extension: str = "") -> Path:
        """Copy file into cache atomically and evict old files if needed."""
        dest_path = self.get_path(key, extension)
        src_size = os.path.getsize(src_path)
        self.evict_if_needed(src_size)

        temp_dest = dest_path.with_name(f"{dest_path.stem}.tmp_{uuid.uuid4().hex[:8]}{dest_path.suffix}")
        shutil.copy2(src_path, temp_dest)
        return self.commit(key, temp_dest, extension)

    def get_total_size(self) -> int:
        """Calculate total size of valid audio cache files in bytes using SQLite index."""
        try:
            with self._get_db_connection() as conn:
                cur = conn.execute("SELECT COALESCE(SUM(file_size), 0) as total FROM audio_cache_entries")
                row = cur.fetchone()
                if row and row["total"] > 0:
                    return int(row["total"])
        except Exception:
            pass

        # Filesystem fallback
        total = 0
        try:
            for entry in self.cache_dir.iterdir():
                if entry.is_file() and not entry.name.startswith(".tmp_"):
                    try:
                        total += entry.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
        return total

    def evict_if_needed(self, additional_bytes: int = 0):
        """
        Evict least recently accessed eligible files until cache is below 90% of max capacity.
        Never evicts active streams or pinned files. Handles oversized files gracefully.
        """
        current_size = self.get_total_size()
        target_size = int(self.max_size_bytes * 0.90)

        # If file is larger than entire capacity, avoid endless loops and evict down to target
        if additional_bytes > self.max_size_bytes:
            logger.warning(f"Incoming file ({additional_bytes} bytes) exceeds total cache capacity ({self.max_size_bytes} bytes)")
            target_size = 0  # Evict as much as possible

        if current_size + additional_bytes <= self.max_size_bytes:
            return

        logger.info(
            f"Audio cache capacity exceeded ({current_size + additional_bytes} > {self.max_size_bytes} bytes). "
            f"Triggering LRU eviction towards {target_size} bytes."
        )

        try:
            with self._get_db_connection() as conn:
                # Query candidates ordered by least recently accessed
                cur = conn.execute("""
                    SELECT cache_key, file_path, file_size, is_pinned
                    FROM audio_cache_entries
                    ORDER BY last_accessed ASC
                """)
                candidates = cur.fetchall()

                for row in candidates:
                    key = row["cache_key"]
                    file_size = row["file_size"]
                    file_path = Path(row["file_path"])

                    # Do not evict if file is actively pinned (in-memory stream or DB flag)
                    if self.is_pinned(key) or row["is_pinned"] > 0:
                        logger.debug(f"Skipping eviction for pinned cache file: {key}")
                        continue

                    # Safe file removal
                    try:
                        if file_path.exists() and file_path.is_file():
                            file_path.unlink(missing_ok=True)
                            logger.info(f"Evicted LRU audio cache file: {file_path.name} ({file_size} bytes)")
                        conn.execute("DELETE FROM audio_cache_entries WHERE cache_key = ?", (key,))
                        current_size -= file_size
                    except OSError as e:
                        logger.error(f"Failed to evict file {file_path}: {e}")

                    if current_size + additional_bytes <= target_size:
                        break
        except Exception as e:
            logger.error(f"Error during cache eviction: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Return cache statistics from SQLite index."""
        try:
            with self._get_db_connection() as conn:
                cur = conn.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(file_size), 0) as total FROM audio_cache_entries")
                row = cur.fetchone()
                count = row["cnt"] if row else 0
                total_size = row["total"] if row else 0
                return {
                    "file_count": count,
                    "total_bytes": total_size,
                    "total_mb": round(total_size / (1024 * 1024), 2),
                    "max_mb": round(self.max_size_bytes / (1024 * 1024), 2),
                    "active_pins": len(self._pinned_counts),
                }
        except Exception:
            return {
                "file_count": 0,
                "total_bytes": 0,
                "total_mb": 0.0,
                "max_mb": round(self.max_size_bytes / (1024 * 1024), 2),
                "active_pins": len(self._pinned_counts),
            }
