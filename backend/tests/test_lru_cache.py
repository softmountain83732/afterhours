"""Comprehensive unit tests for Bounded On-Demand LRU Audio Cache."""

import os
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.cache import FileCache


@pytest.fixture
def temp_cache(tmp_path):
    """Create a temporary cache with 1 MB maximum capacity for deterministic testing."""
    cache_dir = tmp_path / "audio"
    cache = FileCache(cache_dir=str(cache_dir), max_size_mb=1)  # 1 MB = 1,048,576 bytes
    return cache


def test_cache_hit_updates_access_timestamp(temp_cache):
    """Verify that accessing a cached file updates last_accessed in SQLite index and filesystem."""
    key = "track_alpha_standard"
    dest = temp_cache.get_path(key, "m4a")

    with patch("app.services.audio.AudioService.validate_audio_file", return_value=True):
        dest.write_bytes(b"\x00" * 50000)
        temp_cache.reconcile_index()

        with temp_cache._get_db_connection() as conn:
            row = conn.execute("SELECT last_accessed, access_count FROM audio_cache_entries WHERE cache_key = ?", (key,)).fetchone()
            assert row is not None
            initial_access = row["last_accessed"]
            assert row["access_count"] == 1

        time.sleep(0.05)

        retrieved = temp_cache.get(key, "m4a", validate_media=True)
        assert retrieved is not None
        assert retrieved.exists()

        with temp_cache._get_db_connection() as conn:
            row = conn.execute("SELECT last_accessed, access_count FROM audio_cache_entries WHERE cache_key = ?", (key,)).fetchone()
            assert row["last_accessed"] >= initial_access
            assert row["access_count"] == 2


def test_lru_eviction_order_and_capacity_bound(temp_cache):
    """
    Verify least recently accessed eligible files are evicted first until <= 90% capacity,
    while frequently accessed files remain.
    """
    file1 = temp_cache.get_path("track_1_standard", "m4a")
    file2 = temp_cache.get_path("track_2_standard", "m4a")
    file3 = temp_cache.get_path("track_3_standard", "m4a")

    chunk_400k = b"\x00" * 400000

    with patch("app.services.audio.AudioService.validate_audio_file", return_value=True):
        file1.write_bytes(chunk_400k)
        file2.write_bytes(chunk_400k)
        file3.write_bytes(chunk_400k)

        temp_cache.reconcile_index()

        # Manually adjust timestamps to establish strict ordering: file1 (oldest), file2, file3 (newest)
        with temp_cache._get_db_connection() as conn:
            conn.execute("UPDATE audio_cache_entries SET last_accessed = 1000.0 WHERE cache_key = 'track_1_standard'")
            conn.execute("UPDATE audio_cache_entries SET last_accessed = 2000.0 WHERE cache_key = 'track_2_standard'")
            conn.execute("UPDATE audio_cache_entries SET last_accessed = 3000.0 WHERE cache_key = 'track_3_standard'")

        total_size = temp_cache.get_total_size()
        assert total_size == 1200000
        assert total_size > temp_cache.max_size_bytes

        # Trigger eviction
        temp_cache.evict_if_needed(additional_bytes=0)

        # File 1 (least recently accessed) must have been evicted first!
        assert not file1.exists()
        # File 3 (most recently accessed) must remain!
        assert file3.exists()

        # Total size must now be <= 90% of 1 MB (~943,718 bytes)
        target_90pct = int(temp_cache.max_size_bytes * 0.90)
        assert temp_cache.get_total_size() <= target_90pct


def test_active_stream_pinning_prevents_eviction(temp_cache):
    """Verify that a pinned cache file (currently being streamed) is never evicted even if oldest."""
    file_old = temp_cache.get_path("track_old_standard", "m4a")
    file_new = temp_cache.get_path("track_new_standard", "m4a")

    chunk_600k = b"\x00" * 600000

    with patch("app.services.audio.AudioService.validate_audio_file", return_value=True):
        file_old.write_bytes(chunk_600k)
        file_new.write_bytes(chunk_600k)

        temp_cache.reconcile_index()

        with temp_cache._get_db_connection() as conn:
            conn.execute("UPDATE audio_cache_entries SET last_accessed = 100.0 WHERE cache_key = 'track_old_standard'")
            conn.execute("UPDATE audio_cache_entries SET last_accessed = 200.0 WHERE cache_key = 'track_new_standard'")

        # Pin track_old as active stream
        with temp_cache.pin_context("track_old_standard"):
            assert temp_cache.is_pinned("track_old_standard") is True

            # Trigger eviction while track_old is pinned
            temp_cache.evict_if_needed(additional_bytes=0)

            # track_old MUST NOT be evicted because it is actively pinned!
            assert file_old.exists()
            # track_new had to be evicted instead
            assert not file_new.exists()

        # After exiting context, track_old is unpinned
        assert temp_cache.is_pinned("track_old_standard") is False


def test_multiple_quality_variants_handled_independently(temp_cache):
    """Verify that different quality variants for the same track are tracked independently."""
    p_standard = temp_cache.get_path("track_x_standard", "m4a")
    p_high = temp_cache.get_path("track_x_high", "m4a")
    p_saver = temp_cache.get_path("track_x_saver", "m4a")

    with patch("app.services.audio.AudioService.validate_audio_file", return_value=True):
        p_standard.write_bytes(b"\x00" * 10000)
        p_high.write_bytes(b"\x00" * 20000)
        p_saver.write_bytes(b"\x00" * 5000)

        temp_cache.reconcile_index()

        with temp_cache._get_db_connection() as conn:
            rows = conn.execute("SELECT cache_key, quality, file_size FROM audio_cache_entries WHERE track_id = 'track_x' ORDER BY quality").fetchall()
            assert len(rows) == 3
            qualities = [r["quality"] for r in rows]
            assert "standard" in qualities
            assert "high" in qualities
            assert "saver" in qualities


def test_atomic_commit_and_reconciliation(temp_cache, tmp_path):
    """Verify commit moves temporary files atomically and registers in index."""
    temp_file = tmp_path / "temp_encode.m4a"
    temp_file.write_bytes(b"\x00" * 25000)

    key = "track_atomic_standard"
    committed = temp_cache.commit(
        key=key,
        temp_path=temp_file,
        extension="m4a",
        track_id="track_atomic",
        quality="standard"
    )

    assert committed.exists()
    assert not temp_file.exists()  # Moved
    assert committed.stat().st_size == 25000

    # Verify SQLite index has entry
    with temp_cache._get_db_connection() as conn:
        row = conn.execute("SELECT * FROM audio_cache_entries WHERE cache_key = ?", (key,)).fetchone()
        assert row is not None
        assert row["track_id"] == "track_atomic"
        assert row["quality"] == "standard"
        assert row["file_size"] == 25000


def test_oversized_file_does_not_infinite_loop(temp_cache, tmp_path):
    """Verify that an incoming file larger than total cache capacity handles gracefully without looping."""
    oversized = tmp_path / "huge.m4a"
    oversized.write_bytes(b"\x00" * 2000000)  # 2 MB > 1 MB cache capacity

    key = "track_huge_standard"
    committed = temp_cache.commit(
        key=key,
        temp_path=oversized,
        extension="m4a",
        track_id="track_huge",
        quality="standard"
    )
    assert committed.exists()
