"""LRU File Cache for audio files and artwork."""

import os
import time
import shutil
from pathlib import Path
from typing import Optional, Dict, Any


class FileCache:
    """Least-Recently-Used file cache stored on disk."""

    def __init__(self, cache_dir: str, max_size_mb: int = 5000):
        self.cache_dir = Path(cache_dir)
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_path(self, key: str, extension: str = "") -> Path:
        """Construct file path for a key."""
        clean_key = "".join(c for c in key if c.isalnum() or c in ("-", "_"))
        ext = f".{extension.lstrip('.')}" if extension else ""
        return self.cache_dir / f"{clean_key}{ext}"

    def exists(self, key: str, extension: str = "") -> bool:
        """Check if file exists in cache."""
        path = self.get_path(key, extension)
        if path.exists() and path.is_file():
            # Update access time
            os.utime(path, None)
            return True
        return False

    def get(self, key: str, extension: str = "") -> Optional[Path]:
        """Return cached file path and update access time, or None if not found."""
        path = self.get_path(key, extension)
        if path.exists() and path.is_file():
            os.utime(path, None)
            return path
        return None

    def put(self, key: str, src_path: Path, extension: str = "") -> Path:
        """Copy file into cache and evict old files if needed."""
        dest_path = self.get_path(key, extension)
        self.evict_if_needed(os.path.getsize(src_path))
        shutil.copy2(src_path, dest_path)
        os.utime(dest_path, None)
        return dest_path

    def get_total_size(self) -> int:
        """Calculate total size of cache in bytes."""
        total = 0
        for entry in self.cache_dir.iterdir():
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
        return total

    def evict_if_needed(self, additional_bytes: int = 0):
        """Evict oldest accessed files until cache is under capacity."""
        current_size = self.get_total_size()
        target_size = self.max_size_bytes - additional_bytes

        if current_size <= target_size:
            return

        # Sort files by atime ascending
        files = []
        for entry in self.cache_dir.iterdir():
            if entry.is_file():
                try:
                    stat = entry.stat()
                    files.append((stat.st_atime, stat.st_size, entry))
                except OSError:
                    pass

        files.sort(key=lambda x: x[0])

        for _, size, path in files:
            if current_size <= target_size:
                break
            try:
                path.unlink(missing_ok=True)
                current_size -= size
            except OSError:
                pass

    def get_stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        count = 0
        total_size = 0
        for entry in self.cache_dir.iterdir():
            if entry.is_file():
                count += 1
                total_size += entry.stat().st_size
        return {
            "file_count": count,
            "total_bytes": total_size,
            "total_mb": round(total_size / (1024 * 1024), 2),
            "max_mb": round(self.max_size_bytes / (1024 * 1024), 2),
        }
