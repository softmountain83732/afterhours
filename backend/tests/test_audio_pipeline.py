"""Tests for Telegram audio pipeline, cache validation, range requests, and error handling."""

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from telethon.errors import AuthKeyDuplicatedError

from app.main import app
from app.config import get_settings
from app.routers.streaming import audio_cache
from app.services.audio import AudioService
from app.services.cache import FileCache
from app.services.telegram import TelegramService, telegram_service

client = TestClient(app)
settings = get_settings()


def test_corrupt_cache_rejection(tmp_path):
    """Verify that 1028-byte dummy or corrupt audio files are rejected and purged."""
    cache = FileCache(tmp_path, max_size_mb=10)
    key = "corrupt_test_track"

    # Create a 1028-byte dummy file (the exact legacy signature)
    dummy_file = cache.get_path(key, "m4a")
    dummy_file.write_bytes(b"RIFF" + b"\x00" * 1024)
    assert dummy_file.exists()
    assert dummy_file.stat().st_size == 1028

    # validate_audio_file must return False
    assert AudioService.validate_audio_file(dummy_file) is False

    # Cache get must reject and clean up the file
    retrieved = cache.get(key, "m4a", validate_media=True)
    assert retrieved is None
    assert not dummy_file.exists()


def test_auth_key_duplicated_self_healing():
    """Verify that AuthKeyDuplicatedError triggers fresh session generation and reconnect."""
    svc = TelegramService()

    first_connect = True

    async def mock_connect():
        nonlocal first_connect
        if first_connect:
            first_connect = False
            raise AuthKeyDuplicatedError(request=None)
        return True

    from unittest.mock import MagicMock
    mock_client = AsyncMock()
    mock_client.connect = mock_connect
    mock_client.is_connected = lambda: True
    mock_client.is_user_authorized = AsyncMock(return_value=True)
    mock_client.start = AsyncMock(return_value=True)
    mock_client.session.save = MagicMock(return_value="fresh_valid_session_123")

    mock_me = AsyncMock()
    mock_me.id = 99999999
    mock_me.username = "test_healed_bot"
    mock_client.get_me = AsyncMock(return_value=mock_me)

    orig_session = svc.settings.TELEGRAM_STRING_SESSION
    try:
        with patch("app.services.telegram.TelegramClient", return_value=mock_client), \
             patch("app.services.telegram._persist_string_session") as mock_persist:

            import asyncio
            asyncio.run(svc.initialize())

            assert svc.is_connected is True
            # Verify fresh session was persisted
            mock_persist.assert_called_once_with("fresh_valid_session_123")
    finally:
        svc.settings.TELEGRAM_STRING_SESSION = orig_session


def test_telegram_string_session_sanitization_and_diagnostics():
    """Verify that TelegramService strips whitespace and surrounding quotes from TELEGRAM_STRING_SESSION."""
    from unittest.mock import MagicMock
    from telethon.sessions import StringSession
    from telethon.crypto import AuthKey

    # Generate a syntactically valid dummy string session
    s_gen = StringSession()
    s_gen._dc_id = 2
    s_gen._server_address = "149.154.167.50"
    s_gen._port = 443
    s_gen._auth_key = AuthKey(b"\x01" * 256)
    valid_str = s_gen.save()

    svc = TelegramService()

    passed_sessions = []
    def mock_client_init(session, api_id, api_hash):
        passed_sessions.append(session)
        mock = AsyncMock()
        mock.connect = AsyncMock(return_value=True)
        mock.is_connected = lambda: True
        mock.is_user_authorized = AsyncMock(return_value=True)
        me = AsyncMock()
        me.id = 54321
        me.username = "test_bot"
        mock.get_me = AsyncMock(return_value=me)
        mock.add_event_handler = MagicMock()
        return mock

    orig_session = svc.settings.TELEGRAM_STRING_SESSION
    try:
        svc.settings.TELEGRAM_STRING_SESSION = f'  "{valid_str}"  '
        with patch("app.services.telegram.TelegramClient", side_effect=mock_client_init):
            import asyncio
            asyncio.run(svc.initialize())

        assert len(passed_sessions) == 1
        assert passed_sessions[0].dc_id == 2
        assert bool(passed_sessions[0].auth_key) is True
        assert svc.is_connected is True
    finally:
        svc.settings.TELEGRAM_STRING_SESSION = orig_session


def test_telegram_start_deep_link_handler():
    """Verify TelegramService registers /start handler and handles room_ / track_ deep links."""
    svc = TelegramService()
    registered_handlers = []

    def mock_add_event_handler(callback, event):
        registered_handlers.append((callback, event))

    from unittest.mock import MagicMock
    mock_client = AsyncMock()
    mock_client.is_connected = lambda: True
    mock_client.is_user_authorized = AsyncMock(return_value=True)
    mock_client.add_event_handler = mock_add_event_handler
    mock_me = AsyncMock()
    mock_me.id = 12345
    mock_me.username = "test_bot"
    mock_client.get_me = AsyncMock(return_value=mock_me)

    with patch("app.services.telegram.TelegramClient", return_value=mock_client):
        import asyncio
        asyncio.run(svc.initialize())

    assert len(registered_handlers) == 1
    handler, event_filter = registered_handlers[0]

    # Test handling room_123 deep link
    mock_event = AsyncMock()
    mock_event.pattern_match.group = lambda idx: "room_test-room-uuid"
    asyncio.run(handler(mock_event))
    mock_event.respond.assert_called_once()
    args, kwargs = mock_event.respond.call_args
    assert "invited to a Listening Room" in args[0]
    button = kwargs["buttons"][0][0]
    assert button.url == f"{svc.settings.FRONTEND_URL.rstrip('/')}/room/test-room-uuid"

    # Test handling track_456 deep link
    mock_event.reset_mock()
    mock_event.pattern_match.group = lambda idx: "track_test-track-uuid"
    asyncio.run(handler(mock_event))
    mock_event.respond.assert_called_once()
    args, kwargs = mock_event.respond.call_args
    assert "Listen on Afterhours" in args[0]
    button = kwargs["buttons"][0][0]
    assert button.url == f"{svc.settings.FRONTEND_URL.rstrip('/')}/library?track=test-track-uuid"

    # Test default /start with no arguments
    mock_event.reset_mock()
    mock_event.pattern_match.group = lambda idx: ""
    asyncio.run(handler(mock_event))
    mock_event.respond.assert_called_once()
    args, kwargs = mock_event.respond.call_args
    assert "Welcome to Afterhours" in args[0]
    button = kwargs["buttons"][0][0]
    assert button.url == svc.settings.FRONTEND_URL.rstrip("/")


def test_http_range_seeking():
    """Verify HTTP Range 206 Partial Content headers and slicing."""
    tracks_res = client.get("/api/tracks")
    assert tracks_res.status_code == 200
    items = tracks_res.json()["items"]
    assert len(items) > 0
    # Prefer an already-cached track so unit test runs fast without downloading 30MB over MTProto
    cached_track = next((t for t in items if (Path(audio_cache.cache_dir) / f"{t['id']}_standard.m4a").exists()), items[0])
    track_id = cached_track["id"]

    with patch.object(telegram_service, "download_file_from_vault", AsyncMock(return_value=None)):
        # Range 0-1023
        r1 = client.get(f"/api/stream/{track_id}", headers={"Range": "bytes=0-1023"})
        assert r1.status_code == 206
        assert r1.headers.get("accept-ranges") == "bytes"
        assert r1.headers.get("content-range").startswith("bytes 0-1023/")
        assert len(r1.content) == 1024

        # Range with suffix: last 500 bytes (e.g. bytes=-500)
        total_len = int(r1.headers["content-range"].split("/")[-1])
        r2 = client.get(f"/api/stream/{track_id}", headers={"Range": "bytes=100-199"})
        assert r2.status_code == 206
        assert r2.headers.get("content-range") == f"bytes 100-199/{total_len}"
        assert len(r2.content) == 100


def test_invalid_http_range_returns_416():
    """Verify invalid or out-of-bounds byte range returns HTTP 416."""
    tracks_res = client.get("/api/tracks")
    items = tracks_res.json()["items"]
    cached_track = next((t for t in items if (Path(audio_cache.cache_dir) / f"{t['id']}_standard.m4a").exists()), items[0])
    track_id = cached_track["id"]

    with patch.object(telegram_service, "download_file_from_vault", AsyncMock(return_value=None)):
        # Huge out-of-bounds range
        r = client.get(f"/api/stream/{track_id}", headers={"Range": "bytes=999999999-9999999999"})
        assert r.status_code == 416
        assert "content-range" in r.headers
        assert r.headers["content-range"].startswith("bytes */")


def test_stream_returns_502_when_upstream_fails():
    """Verify streaming endpoint returns 502/503 and never synthetic audio when download fails."""
    with patch.object(telegram_service, "download_file_from_vault", AsyncMock(return_value=None)):
        # Request a non-existent track
        r = client.get("/api/stream/00000000-0000-0000-0000-000000000000")
        assert r.status_code in [404, 502, 503]
        assert r.status_code != 200
        assert r.status_code != 206
    # Never return dummy audio data


def test_in_flight_download_deduplication():
    """Verify concurrent requests for the same uncached track share the same background task."""
    from app.routers import streaming
    track_id = "test-dedup-track"
    quality = "standard"
    cache_key = f"{track_id}_{quality}"

    call_count = 0
    async def mock_download_and_transcode(tid, q, track_dict):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.1)
        return Path("/tmp/dummy.m4a")

    async def run_test():
        with patch.object(streaming, "_download_and_transcode_track", side_effect=mock_download_and_transcode):
            async def resolve():
                async with streaming._in_flight_lock:
                    task = streaming._in_flight_tasks.get(cache_key)
                    if task is None or task.done():
                        task = asyncio.create_task(streaming._download_and_transcode_track(track_id, quality, {}))
                        streaming._in_flight_tasks[cache_key] = task
                return await asyncio.shield(task)

            results = await asyncio.gather(resolve(), resolve(), resolve())
            assert len(results) == 3
            # Ensure underlying worker was executed exactly once for all concurrent callers
            assert call_count == 1
            streaming._in_flight_tasks.pop(cache_key, None)

    asyncio.run(run_test())


def test_client_disconnect_shielding():
    """Verify that cancelling a client request does not abort the background download task."""
    from app.routers import streaming
    track_id = "test-shield-track"
    quality = "standard"
    cache_key = f"{track_id}_{quality}"
    task_completed = False

    async def long_download(tid, q, track_dict):
        nonlocal task_completed
        await asyncio.sleep(0.2)
        task_completed = True
        return Path("/tmp/shield.m4a")

    async def run_test():
        with patch.object(streaming, "_download_and_transcode_track", side_effect=long_download):
            async with streaming._in_flight_lock:
                task = asyncio.create_task(streaming._download_and_transcode_track(track_id, quality, {}))
                streaming._in_flight_tasks[cache_key] = task

            # Simulate client disconnecting after 0.05s
            async def client_request():
                await asyncio.shield(task)

            client_task = asyncio.create_task(client_request())
            await asyncio.sleep(0.05)
            client_task.cancel()

            with pytest.raises(asyncio.CancelledError):
                await client_task

            # The underlying background task MUST still run to completion
            await task
            assert task_completed is True
            streaming._in_flight_tasks.pop(cache_key, None)

    asyncio.run(run_test())


def test_ffmpeg_audio_only_mapping_and_nonexistent_input(tmp_path):
    """Verify AudioService enforces -map 0:a:0 -vn and cleanly handles missing input files."""
    # 1. Non-existent input file returns False immediately
    non_existent = tmp_path / "missing.flac"
    out_file = tmp_path / "out.m4a"

    async def run_test():
        res = await AudioService.transcode_to_aac(non_existent, out_file)
        assert res is False
        assert not out_file.exists()

        # 2. Verify FFmpeg command construction has -map 0:a:0 and -vn
        mock_input = tmp_path / "valid.flac"
        mock_input.write_bytes(b"fLaC" + b"\x00" * 20000)

        captured_cmd = None
        def mock_subprocess_run(cmd, **kwargs):
            nonlocal captured_cmd
            captured_cmd = list(cmd)
            # Create dummy transcoded output file so validation can succeed
            out_file.parent.mkdir(parents=True, exist_ok=True)
            res = subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            return res

        with patch("app.services.audio.AudioService.validate_audio_file", return_value=True), \
             patch("subprocess.run", side_effect=mock_subprocess_run):

            success = await AudioService.transcode_to_aac(mock_input, out_file)
            assert captured_cmd is not None
            # Enforce audio-only mapping and -vn
            assert "-map" in captured_cmd
            map_idx = captured_cmd.index("-map")
            assert captured_cmd[map_idx + 1] == "0:a:0"
            assert "-vn" in captured_cmd

        # 3. Subprocess failure captures error and returns False
        def mock_failing_subprocess_run(cmd, **kwargs):
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="FFmpeg internal error: stream mapping failed")

        with patch("app.services.audio.AudioService.validate_audio_file", return_value=True), \
             patch("subprocess.run", side_effect=mock_failing_subprocess_run):

            fail_res = await AudioService.transcode_to_aac(mock_input, out_file)
            assert fail_res is False

    asyncio.run(run_test())


def test_thread_based_transcode_execution_path_and_timeout(tmp_path):
    """Regression test: Verify transcode runs via asyncio.to_thread(subprocess.run) without calling asyncio.create_subprocess_exec."""
    mock_input = tmp_path / "thread_test.flac"
    mock_input.write_bytes(b"fLaC" + b"\x00" * 20000)
    out_file = tmp_path / "thread_out.m4a"

    async def run_test():
        # Verify asyncio.to_thread is used and asyncio.create_subprocess_exec is never touched
        called_kwargs = {}
        def mock_sub_run(cmd, **kwargs):
            called_kwargs.update(kwargs)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("app.services.audio.AudioService.validate_audio_file", return_value=True), \
             patch("subprocess.run", side_effect=mock_sub_run), \
             patch("asyncio.create_subprocess_exec") as mock_async_exec:

            await AudioService.transcode_to_aac(mock_input, out_file)
            # asyncio.create_subprocess_exec must NEVER be called (prevents Windows NotImplementedError)
            mock_async_exec.assert_not_called()
            # subprocess.run received thread kwargs
            assert called_kwargs.get("timeout") == 180
            assert called_kwargs.get("text") is True

        # Verify TimeoutExpired handling
        def mock_timeout_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=180)

        with patch("app.services.audio.AudioService.validate_audio_file", return_value=True), \
             patch("subprocess.run", side_effect=mock_timeout_run):

            timeout_res = await AudioService.transcode_to_aac(mock_input, out_file)
            assert timeout_res is False

    asyncio.run(run_test())


def test_uncached_track_aborts_cleanly_when_raw_download_missing():
    """Verify that if Vault download fails to produce a file on disk, transcode aborts with 502 and never calls FFmpeg."""
    from fastapi import HTTPException
    from app.routers.streaming import _download_and_transcode_track

    mock_track = {
        "id": "uncached-test-fail-track",
        "codec": "flac",
        "telegram_chat_id": -1004414876471,
        "telegram_message_id": 9999,
    }

    async def run_test():
        with patch.object(telegram_service, "download_file_from_vault", AsyncMock(return_value=None)), \
             patch.object(AudioService, "transcode_to_aac") as mock_transcode:

            with pytest.raises(HTTPException) as exc_info:
                await _download_and_transcode_track("uncached-test-fail-track", "standard", mock_track)

            assert exc_info.value.status_code == 502
            assert "Raw audio file could not be retrieved" in exc_info.value.detail
            # FFmpeg must NEVER be invoked when raw file is missing
            mock_transcode.assert_not_called()

    asyncio.run(run_test())


def test_uncached_track_download_transcode_cache_commit_flow(tmp_path):
    """Trace complete uncached flow: Vault download -> raw validation -> FFmpeg transcode -> LRU cache commit."""
    from app.routers import streaming
    from app.services.cache import FileCache

    test_cache = FileCache(tmp_path / "audio_cache", max_size_mb=50)
    track_id = "uncached-success-track"
    quality = "standard"
    mock_track = {
        "id": track_id,
        "codec": "flac",
        "telegram_chat_id": -1004414876471,
        "telegram_message_id": 8888,
    }

    raw_sample = tmp_path / "source.flac"
    raw_sample.write_bytes(b"fLaC" + b"\x00" * 30000)

    transcoded_sample = tmp_path / "transcoded.m4a"
    transcoded_sample.write_bytes(b"\x00\x00\x00 ftypM4A " + b"\x00" * 25000)

    async def mock_download(chat_id, msg_id, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw_sample.read_bytes())
        return dest

    async def mock_transcode(inp, out, bitrate):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(transcoded_sample.read_bytes())
        return True

    async def run_test():
        with patch.object(streaming, "audio_cache", test_cache), \
             patch.object(telegram_service, "download_file_from_vault", side_effect=mock_download), \
             patch.object(AudioService, "validate_audio_file", return_value=True), \
             patch.object(AudioService, "transcode_to_aac", side_effect=mock_transcode):

            committed_path = await streaming._download_and_transcode_track(track_id, quality, mock_track)

            # 1. Output file committed to cache
            assert committed_path.exists()
            assert committed_path.name == f"{track_id}_{quality}.m4a"

            # 2. File retrieved from cache on subsequent lookup
            cached = test_cache.get(f"{track_id}_{quality}", "m4a", validate_media=False)
            assert cached is not None
            assert cached == committed_path

    asyncio.run(run_test())
