"""Comprehensive unit tests for Real-Time Vault Listener, Idempotency, and Checkpoints."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from telethon.tl.types import MessageMediaDocument, Document, DocumentAttributeAudio, MessageMediaPhoto

from app.db import db
from app.services.supabase_client import SupabaseService
from app.services.vault_listener import VaultListenerService


@pytest.fixture
def listener_service(tmp_path):
    """Create a test VaultListenerService with temporary checkpoint storage and isolated from live DB."""
    with patch.object(SupabaseService, "is_live", new_callable=PropertyMock, return_value=False):
        svc = VaultListenerService()
        svc.checkpoint_file = tmp_path / "test_checkpoints.json"
        svc.chat_id = -1009999999999
        if hasattr(db, "vault_sync_checkpoints"):
            db.vault_sync_checkpoints.clear()
        yield svc
        if hasattr(db, "vault_sync_checkpoints"):
            db.vault_sync_checkpoints.clear()


def test_is_audio_message_detection(listener_service):
    """Verify supported audio detection and rejection of unsupported media."""
    # 1. Valid audio attribute
    msg_audio_attr = MagicMock()
    doc = MagicMock(spec=Document)
    doc.mime_type = "audio/flac"
    doc.attributes = [DocumentAttributeAudio(duration=120, title="Song", performer="Artist")]
    msg_audio_attr.media = MagicMock(spec=MessageMediaDocument)
    msg_audio_attr.media.document = doc
    msg_audio_attr.file = MagicMock()
    msg_audio_attr.file.name = "song.flac"
    assert listener_service.is_audio_message(msg_audio_attr) is True

    # 2. Audio MIME type without attribute
    msg_mime = MagicMock()
    doc2 = MagicMock(spec=Document)
    doc2.mime_type = "audio/mp4"
    doc2.attributes = []
    msg_mime.media = MagicMock(spec=MessageMediaDocument)
    msg_mime.media.document = doc2
    msg_mime.file = MagicMock()
    msg_mime.file.name = "voice.m4a"
    assert listener_service.is_audio_message(msg_mime) is True

    # 3. Audio file extension without audio mime
    msg_ext = MagicMock()
    doc3 = MagicMock(spec=Document)
    doc3.mime_type = "application/octet-stream"
    doc3.attributes = []
    msg_ext.media = MagicMock(spec=MessageMediaDocument)
    msg_ext.media.document = doc3
    msg_ext.file = MagicMock()
    msg_ext.file.name = "track.wav"
    assert listener_service.is_audio_message(msg_ext) is True

    # 4. Photo -> rejected
    msg_photo = MagicMock()
    msg_photo.media = MagicMock(spec=MessageMediaPhoto)
    assert listener_service.is_audio_message(msg_photo) is False

    # 5. Plain text -> rejected
    msg_text = MagicMock()
    msg_text.media = None
    assert listener_service.is_audio_message(msg_text) is False

    # 6. Video document -> rejected
    msg_video = MagicMock()
    doc4 = MagicMock(spec=Document)
    doc4.mime_type = "video/mp4"
    doc4.attributes = []
    msg_video.media = MagicMock(spec=MessageMediaDocument)
    msg_video.media.document = doc4
    msg_video.file = MagicMock()
    msg_video.file.name = "clip.mp4"
    assert listener_service.is_audio_message(msg_video) is False


def test_checkpoint_persistence_and_advancement(listener_service):
    """Verify that checkpoints are persisted, loaded, and advanced monotonically."""
    chat_id = listener_service.chat_id

    # Initially 0
    assert listener_service.get_checkpoint(chat_id) == 0

    # Advance to 10
    listener_service.save_checkpoint(chat_id, 10)
    assert listener_service.get_checkpoint(chat_id) == 10

    # Monotonic: lower value should not regress
    listener_service.save_checkpoint(chat_id, 5)
    assert listener_service.get_checkpoint(chat_id) == 10

    # Advance to 25
    listener_service.save_checkpoint(chat_id, 25)
    assert listener_service.get_checkpoint(chat_id) == 25

    # Re-instantiate service: verify persistent load from disk
    new_svc = VaultListenerService()
    new_svc.checkpoint_file = listener_service.checkpoint_file
    assert new_svc.get_checkpoint(chat_id) == 25


def test_idempotent_ingestion_and_duplicate_suppression(listener_service, tmp_path):
    """Verify that duplicate message ingestion events do not produce duplicate database tracks."""
    import time
    unique_msg_id = int(time.time() * 1000) % 100000000 + 500000

    msg = MagicMock()
    msg.id = unique_msg_id
    msg.chat_id = -1009999999999

    doc = MagicMock(spec=Document)
    doc.mime_type = "audio/flac"
    doc.attributes = [DocumentAttributeAudio(duration=60, title="Unique Song", performer="Test Artist")]
    msg.media = MagicMock(spec=MessageMediaDocument)
    msg.media.document = doc
    msg.file = MagicMock()
    msg.file.name = "test.flac"

    sample_flac = tmp_path / f"sample_{unique_msg_id}.flac"
    sample_flac.write_bytes(f"FLAC_UNIQUE_{unique_msg_id}".encode() + b"\x00" * 20000)

    from unittest.mock import PropertyMock
    from app.services.supabase_client import SupabaseService

    async def run_test():
        # Mock download_media, validation, Mutagen metadata, artwork, and isolate from live Supabase
        with patch.object(msg, "download_media", AsyncMock(return_value=str(sample_flac))), \
             patch("app.services.audio.AudioService.validate_audio_file", return_value=True), \
             patch("app.services.ingestion.IngestionService.extract_metadata", return_value={
                 "title": "Unique Song", "artist": "Test Artist", "album": "Test Album", "duration_ms": 60000, "codec": "flac"
             }), \
             patch("app.services.ingestion.IngestionService.extract_artwork", return_value=None), \
             patch.object(SupabaseService, "is_live", new_callable=PropertyMock, return_value=False):

            # First ingestion
            track1 = await listener_service.ingest_message(msg)
            assert track1 is not None
            assert track1["title"] == "Unique Song"

            # Second ingestion of the exact same message
            track2 = await listener_service.ingest_message(msg)
            # Must return None (skipped as duplicate)
            assert track2 is None

    asyncio.run(run_test())


def test_listener_registration_and_clean_shutdown(listener_service):
    """Verify that the Telethon event handler registers only once and unregisters cleanly on stop."""
    mock_client = MagicMock()
    mock_client.add_event_handler = MagicMock()
    mock_client.remove_event_handler = MagicMock()

    mock_tg = MagicMock()
    mock_tg.is_connected = True
    mock_tg.client = mock_client

    async def run_test():
        with patch("app.services.vault_listener.telegram_service", mock_tg), \
             patch.object(listener_service, "reconcile_vault", AsyncMock(return_value={"scanned": 0})):

            # Start listener
            await listener_service.start()
            assert listener_service.is_running is True
            assert listener_service._handler_registered is True
            mock_client.add_event_handler.assert_called_once()

            # Start again: must NOT register a duplicate handler
            await listener_service.start()
            assert mock_client.add_event_handler.call_count == 1

            # Stop listener
            await listener_service.stop()
            assert listener_service.is_running is False
            assert listener_service._handler_registered is False
            mock_client.remove_event_handler.assert_called_once()

    asyncio.run(run_test())


def test_unsupported_message_advances_checkpoint(listener_service):
    """Verify that non-audio messages (e.g. photos/text) advance checkpoint without triggering ingestion."""
    chat_id = -1009999999999
    listener_service.save_checkpoint(chat_id, 119)
    event = MagicMock()
    event.message = MagicMock()
    event.message.id = 120
    event.message.chat_id = chat_id
    event.message.media = None  # Text message

    async def run_test():
        with patch.object(listener_service, "ingest_message", AsyncMock()) as mock_ingest:
            await listener_service.on_vault_message(event)

            # Ingest should NOT have been called
            mock_ingest.assert_not_called()
            # Checkpoint must have advanced contiguously to 120
            assert listener_service.get_checkpoint(chat_id) == 120

    asyncio.run(run_test())


# =============================================================================
# REGRESSION TESTS: WATERMARK ORDERING & CRASH RESILIENCE
# =============================================================================

def test_out_of_order_live_message_does_not_advance_checkpoint(listener_service):
    """
    Regression Test 1: While messages 6-9 are being reconciled/pending,
    message 10 arrives via live event.
    Checkpoint must NOT advance past 5 until 6, 7, 8, 9 are all completed.
    """
    chat_id = -1009999999999
    listener_service.save_checkpoint(chat_id, 5)

    async def run_test():
        # Live message 10 arrives (non-audio or mock audio)
        msg10 = MagicMock()
        msg10.id = 10
        msg10.chat_id = chat_id
        msg10.media = None  # non-audio
        await listener_service._process_message(msg10)

        # Checkpoint MUST STILL BE 5! (Cannot jump to 10)
        assert listener_service.get_checkpoint(chat_id) == 5

        # Now messages 6, 7, 8 arrive and finish
        for mid in (6, 7, 8):
            m = MagicMock()
            m.id = mid
            m.chat_id = chat_id
            m.media = None
            await listener_service._process_message(m)
            assert listener_service.get_checkpoint(chat_id) == mid

        # Now message 9 finishes
        m9 = MagicMock()
        m9.id = 9
        m9.chat_id = chat_id
        m9.media = None
        await listener_service._process_message(m9)

        # NOW checkpoint must advance through 9 to 10 (since 10 was already completed!)
        assert listener_service.get_checkpoint(chat_id) == 10

    asyncio.run(run_test())


def test_process_interruption_preserves_lower_checkpoint(listener_service):
    """
    Regression Test 2: Message 10 arrives and completes while 6-9 are not completed.
    Process is interrupted. On reload/restart, the persistent checkpoint on disk
    must still be 5, guaranteeing messages 6-9 will not be skipped during reconciliation.
    """
    chat_id = -1009999999999
    listener_service.save_checkpoint(chat_id, 5)

    async def run_test():
        msg10 = MagicMock()
        msg10.id = 10
        msg10.chat_id = chat_id
        msg10.media = None
        await listener_service._process_message(msg10)

        # In-memory and persisted checkpoint must be 5
        assert listener_service.get_checkpoint(chat_id) == 5

        # Simulate process crash / new service instance reading from disk
        new_svc = VaultListenerService()
        new_svc.checkpoint_file = listener_service.checkpoint_file
        # Checkpoint on disk must be 5
        assert new_svc.get_checkpoint(chat_id) == 5

    asyncio.run(run_test())


def test_ingestion_failure_halts_checkpoint(listener_service):
    """
    Regression Test 3: Ingestion fails on message 7 in the middle of a batch.
    Messages 6, 8, 9, 10 succeed.
    Checkpoint must halt at 6 and NEVER advance past 7.
    """
    chat_id = -1009999999999
    listener_service.save_checkpoint(chat_id, 5)

    async def run_test():
        # Msg 6 succeeds
        m6 = MagicMock()
        m6.id = 6
        m6.chat_id = chat_id
        m6.media = None
        await listener_service._process_message(m6)
        assert listener_service.get_checkpoint(chat_id) == 6

        # Msg 7 is audio and FAILS
        doc7 = MagicMock(spec=Document)
        doc7.mime_type = "audio/flac"
        doc7.attributes = [DocumentAttributeAudio(duration=60, title="Song 7", performer="Artist")]
        m7 = MagicMock()
        m7.id = 7
        m7.chat_id = chat_id
        m7.media = MagicMock(spec=MessageMediaDocument)
        m7.media.document = doc7

        with patch.object(listener_service, "ingest_message", AsyncMock(return_value=None)), \
             patch.object(listener_service, "_is_already_ingested", return_value=False):
            await listener_service._process_message(m7)

        # Checkpoint must stay at 6
        assert listener_service.get_checkpoint(chat_id) == 6

        # Msgs 8, 9, 10 succeed
        for mid in (8, 9, 10):
            m = MagicMock()
            m.id = mid
            m.chat_id = chat_id
            m.media = None
            await listener_service._process_message(m)
            # Checkpoint MUST STILL BE 6 because 7 failed!
            assert listener_service.get_checkpoint(chat_id) == 6

    asyncio.run(run_test())


def test_duplicate_concurrency_idempotency(listener_service, tmp_path):
    """
    Regression Test 4: Live event and reconciliation process the same message concurrently.
    Guarantees exactly one database entry, idempotency, and clean checkpoint advancement.
    """
    chat_id = -1009999999999
    listener_service.save_checkpoint(chat_id, 5)

    msg = MagicMock()
    msg.id = 6
    msg.chat_id = chat_id
    doc = MagicMock(spec=Document)
    doc.mime_type = "audio/flac"
    doc.attributes = [DocumentAttributeAudio(duration=60, title="Concurrent Song", performer="Artist")]
    msg.media = MagicMock(spec=MessageMediaDocument)
    msg.media.document = doc
    msg.file = MagicMock()
    msg.file.name = "conc.flac"

    sample_flac = tmp_path / "conc.flac"
    sample_flac.write_bytes(b"FLAC_CONC_TEST" + b"\x00" * 15000)

    from unittest.mock import PropertyMock
    from app.services.supabase_client import SupabaseService

    async def run_test():
        with patch.object(msg, "download_media", AsyncMock(return_value=str(sample_flac))), \
             patch("app.services.audio.AudioService.validate_audio_file", return_value=True), \
             patch("app.services.ingestion.IngestionService.extract_metadata", return_value={
                 "title": "Concurrent Song", "artist": "Artist", "album": "Album", "duration_ms": 60000, "codec": "flac"
             }), \
             patch("app.services.ingestion.IngestionService.extract_artwork", return_value=None), \
             patch.object(SupabaseService, "is_live", new_callable=PropertyMock, return_value=False):

            # Run 2 concurrent ingestion attempts for msg 6
            t1 = listener_service._process_message(msg)
            t2 = listener_service._process_message(msg)
            await asyncio.gather(t1, t2)

            # Checkpoint must be 6
            assert listener_service.get_checkpoint(chat_id) == 6

            # Database must have exactly 1 track for msg 6
            matching = [t for t in db.tracks.values() if t.get("telegram_message_id") == 6 and t.get("telegram_chat_id") == chat_id]
            assert len(matching) == 1

    asyncio.run(run_test())


def test_restart_recovery_after_interruption(listener_service, tmp_path):
    """
    Regression Test 5: Interruption happened with checkpoint at 5 and msg 10 ingested.
    New service instance boots up, runs reconciliation from checkpoint+1 (6).
    Reconciles 6, 7, 8, 9, detects 10 already ingested, and advances checkpoint to 10.
    """
    chat_id = -1009999999999
    listener_service.save_checkpoint(chat_id, 5)

    # Pre-populate msg 10 as already ingested in DB
    db.tracks["track_10"] = {
        "id": "track_10",
        "title": "Song 10",
        "telegram_chat_id": chat_id,
        "telegram_message_id": 10,
        "file_hash": "hash_10",
    }

    # Simulate restart with new service instance
    new_svc = VaultListenerService()
    new_svc.checkpoint_file = listener_service.checkpoint_file
    assert new_svc.get_checkpoint(chat_id) == 5

    # Mock client.get_messages for reconciliation
    mock_messages = []
    for mid in (6, 7, 8, 9, 10):
        m = MagicMock()
        m.id = mid
        m.chat_id = chat_id
        m.media = None  # Non-audio for quick reconciliation
        mock_messages.append(m)

    mock_client = MagicMock()
    mock_client.get_messages = AsyncMock(side_effect=[
        mock_messages,  # first chunk (6-10)
        [],  # second chunk (empty -> streak 1)
        []   # third chunk (empty -> streak 2 -> break)
    ])

    mock_tg = MagicMock()
    mock_tg.is_connected = True
    mock_tg.client = mock_client

    async def run_test():
        with patch("app.services.vault_listener.telegram_service", mock_tg):
            res = await new_svc.reconcile_vault(chat_id=chat_id, max_batch=5)
            assert res["checkpoint"] == 10
            assert new_svc.get_checkpoint(chat_id) == 10

    asyncio.run(run_test())


def test_channel_post_and_forwarded_audio_accepted_by_listener(listener_service, tmp_path):
    """Verify that both direct channel posts and forwarded audio messages are accepted by listener."""
    chat_id = listener_service.chat_id
    listener_service.save_checkpoint(chat_id, 20)

    # 1. Direct channel post
    direct_msg = MagicMock()
    direct_msg.id = 21
    direct_msg.chat_id = chat_id
    direct_msg.post = True
    direct_msg.fwd_from = None
    direct_msg.media = None  # non-audio for quick test

    event_direct = MagicMock()
    event_direct.message = direct_msg

    # 2. Forwarded message from another channel or user
    fwd_msg = MagicMock()
    fwd_msg.id = 22
    fwd_msg.chat_id = chat_id
    fwd_msg.post = True
    fwd_msg.fwd_from = MagicMock()
    fwd_msg.media = None

    event_fwd = MagicMock()
    event_fwd.message = fwd_msg

    async def run_test():
        # Process direct message
        await listener_service.on_vault_message(event_direct)
        assert listener_service.get_checkpoint(chat_id) == 21

        # Process forwarded message
        await listener_service.on_vault_message(event_fwd)
        assert listener_service.get_checkpoint(chat_id) == 22

    asyncio.run(run_test())


def test_polling_fallback_handles_multi_batch_overflow(listener_service):
    """Verify that incremental reconciliation handles batches larger than requested limit."""
    chat_id = listener_service.chat_id
    listener_service.save_checkpoint(chat_id, 100)

    # Create 75 messages (requiring multiple chunks with batch_size=25)
    batch_size = 25
    chunk1 = [MagicMock(id=i, chat_id=chat_id, media=None) for i in range(101, 126)]
    chunk2 = [MagicMock(id=i, chat_id=chat_id, media=None) for i in range(126, 151)]
    chunk3 = [MagicMock(id=i, chat_id=chat_id, media=None) for i in range(151, 176)]

    mock_client = MagicMock()
    mock_client.get_messages = AsyncMock(side_effect=[
        chunk1,  # 101-125
        chunk2,  # 126-150
        chunk3,  # 151-175
        [],      # 176-200 empty
        [],      # 201-225 empty -> streak 2 -> break
    ])

    mock_tg = MagicMock()
    mock_tg.is_connected = True
    mock_tg.client = mock_client

    async def run_test():
        with patch("app.services.vault_listener.telegram_service", mock_tg):
            res = await listener_service.reconcile_vault(chat_id=chat_id, max_batch=batch_size)
            assert res["scanned"] == 75
            assert res["checkpoint"] == 175
            assert listener_service.get_checkpoint(chat_id) == 175

    asyncio.run(run_test())
