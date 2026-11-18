# Afterhours — Project Status

## Current Status: Production MVP Build & Live Verification Complete

All requirements, backend live integrations, Telegram Vault MTProto storage, audio streaming engine, and frontend builds have been fully implemented, integrated, and verified end-to-end.

---

## Completed Implementations & Live Integrations

### 1. Database & Live Supabase Migration
- Schema migration `database/migrations/001_initial_schema.sql` applied cleanly to live Supabase PostgreSQL.
- RLS policies, indexes, foreign keys, and triggers verified.
- Fixed helper function order (`is_room_member`, `is_room_host` defined after `room_members`) to avoid PostgreSQL `42P01` relation errors.
- Real ingested tracks with artist, album, and artwork relations active:
  - `MONTAGEM YUESHI` (WUYS)
  - `弥渡山歌 (Midu Echoing)` (YANGYINYUE)
  - `Genesis` (Grimes)
  - `Poison Tree` (Beth Orton)

### 2. Telegram Vault Storage & MTProto Telethon Engine
- Upgraded `TelegramService` to use a clean `StringSession` (`TELEGRAM_STRING_SESSION`), resolving SQLite multi-column unpack bugs on Python 3.13 / Telethon 1.41.2.
- Verified live connection to Telegram DC 5.
- Implemented bot-safe chunked message retrieval (`get_messages` by ID chunks), bypassing Bot API restrictions on `iter_messages`.
- Live file downloads from Vault channel (`-1004414876471`) verified (e.g. 12.5MB FLAC audio downloaded in seconds).
- Personal uploads to Telegram Vault (`send_to_vault`) fully working and registering live audio messages.

### 3. Real Audio Streaming & FFmpeg AAC Transcoding Engine
- Real-time on-demand audio transcoding: original FLAC/high-resolution files downloaded from Telegram Vault are transcoded to universal AAC (`.m4a`) using FFmpeg with `-movflags +faststart` for zero playback latency.
- Supported quality profiles: Standard (256k), High (320k), and Saver (128k).
- Full HTTP Range support (HTTP 206 Partial Content) with `Accept-Ranges: bytes`, verified seeking and streaming directly to HTML5 `<audio>` elements.
- LRU caching in `AUDIO_CACHE_DIR` avoids repeated Telegram downloads or re-transcoding.

### 4. Router Live Supabase Integration
- **`tracks.py`**: Queries live Supabase with relational joins on `artists`, `albums`, and `track_artwork`, formats according to frontend `Track` interface, filters public tracks and user's own private uploads, supports case-insensitive search, and checks user favorites.
- **`streaming.py`**: Resolves track metadata from Supabase, performs private track access validation (`track_access` table / room membership), downloads from Telegram Vault, transcodes with FFmpeg, and streams with HTTP 206 Range headers.
- **`uploads.py`**: Validates audio formats/size, checks file hashes for deduplication in Supabase, extracts metadata via Mutagen, optimizes embedded cover artwork to WebP thumbnails, sends audio to Telegram Vault, registers `artists`, `albums`, `tracks`, and `track_artwork` in Supabase with `uploaded_by = user_id`.
- **`favorites.py`**: Reads and writes to Supabase `favorites` with foreign key relations to `profiles` and `tracks`.
- **`history.py`**: Reads and logs playback events to Supabase `listening_history`.
- **`playlists.py`**: Full CRUD for playlists and track sequencing in Supabase `playlists` and `playlist_tracks`.
- **`profiles.py`**: Profile setup, statistic aggregations (uploads count, playlists count, favorites count), updates, and public profile views.
- **`rooms.py`**: Real-time room discovery, host control delegation, listener synchronization, chat messages, and track access grants in Supabase `rooms`, `room_members`, and `room_messages`.

### 5. Authentication & Desktop Dev Auth
- **Telegram Mini App Auth (`/api/auth/telegram`)**: Full cryptographic HMAC-SHA256 signature verification of Telegram WebApp `initData`, automatically syncing or creating users in Supabase `auth.users` and `profiles`.
- **Dev Auth (`/api/auth/dev`)**: Secure local development endpoint restricted to localhost (`127.0.0.1`, `localhost`, `::1`) when `DEV_AUTH_ENABLED` is set, automatically authenticating desktop browsers outside Telegram as `afterhours_dev`.
- **Frontend Token Refresh**: Transparent 401 handling in `src/lib/api.ts` with in-flight request deduplication and dev token refresh.

### 6. Spotify Jam-Style Collaborative Listening Rooms (Phase 1 & Phase 2)
- **Authoritative Server Architecture**: FastAPI backend + Supabase PostgreSQL single source of truth with monotonic room revisions (`revision`), server timestamp offsets, and scheduled lead times (500–1500ms, default 800ms).
- **Hardened Atomic RPCs (`004_room_queue_atomicity_rpc.sql`)**:
  - `add_room_queue_item_atomic`: Single-transaction normalized queue insertion with row-level locks and `rooms.queue` dual-write sync.
  - `vote_room_queue_item_atomic`: Dynamic resort with explicit room boundary checks and vote counting.
  - `remove_room_queue_item_atomic`: Safe removal with positional defragmentation.
  - `next_room_track_atomic`: Atomic track advance with playback state initialization.
  - `SECURITY DEFINER` hardened with explicit `SET search_path = public, pg_temp`, `REVOKE ... FROM PUBLIC, anon, authenticated`, `GRANT EXECUTE ... TO service_role`.
- **Realtime Broadcast & Synchronization (CB-01)**:
  - Low-latency Supabase Realtime REST broadcast (`/realtime/v1/api/broadcast`) on channel `room:{room_id}` for `playback_sync`, `queue_updated`, `permissions_updated`, and `host_transferred`.
  - Frontend `useRoomSync` subscribes to postgres changes and broadcast events; polling downgraded from 3000ms to a 15000ms slow fallback when Realtime channel is disconnected.
- **Drift Correction Engine & React Loop Fix (CB-02)**:
  - Four-tier correction: `< 80ms` (in sync), `80-500ms` (mild rate `0.98x-1.02x`), `500-1500ms` (moderate rate `0.95x-1.05x`), `>= 1500ms` (hard seek).
  - Rate state stored in `playbackRateRef`, eliminating React cleanup unmount loops and rate oscillation.
- **Buffered WebKit Metadata Seek (MP-03)**:
  - `safeSeek(targetSeconds)` checks `audio.readyState >= 1`, attaching a one-time `loadedmetadata` listener if metadata is pending to prevent seek drops to `00:00` on iOS Safari and Telegram WebView.
- **Telegram WebApp /start Deep Linking & Menu Button**:
  - Registered `/start` handler in `TelegramService` parsing `room_<id>` and `track_<id>` parameters, returning interactive `KeyboardButtonWebView` buttons.
  - Initialized Telegram bot `BotMenuButton` pointing directly to `${FRONTEND_URL}`.
  - Added frontend `TelegramDeepLinkHandler` and Room page Invite button with `https://t.me/${botUsername}?startapp=room_${roomId}` native share and clipboard fallback.

---

## Verification & Test Results

1. **Backend Integration & Unit Tests**:
   - `python -m pytest backend/tests -v`: **54 passed (100% PASS)**
     - Audio Pipeline & Range Streaming:
       - `test_corrupt_cache_rejection`: PASSED
       - `test_auth_key_duplicated_self_healing`: PASSED
       - `test_telegram_start_deep_link_handler`: PASSED
       - `test_http_range_seeking`: PASSED
       - `test_invalid_http_range_returns_416`: PASSED
       - `test_stream_returns_502_when_upstream_fails`: PASSED
     - Platform Core & User Journeys:
       - `test_health_check`: PASSED
       - `test_telegram_auth_and_profile_flow`: PASSED
       - `test_username_collision_prevention`: PASSED
       - `test_guest_auth_and_limitations`: PASSED
       - `test_track_discovery_and_search`: PASSED
       - `test_audio_streaming_range_and_seeking`: PASSED
       - `test_playlist_lifecycle`: PASSED
       - `test_listening_rooms_journey`: PASSED
       - `test_favorites_and_history`: PASSED
     - Bounded On-Demand LRU Cache:
       - `test_cache_hit_updates_access_timestamp`: PASSED
       - `test_lru_eviction_order_and_capacity_bound`: PASSED
       - `test_active_stream_pinning_prevents_eviction`: PASSED
       - `test_multiple_quality_variants_handled_independently`: PASSED
       - `test_atomic_commit_and_reconciliation`: PASSED
       - `test_oversized_file_does_not_infinite_loop`: PASSED
     - Listening Rooms Jam Sync & Queue RPCs:
       - `test_clock_sync_endpoint`: PASSED
       - `test_scheduled_playback_lead_time_and_bounds`: PASSED
       - `test_monotonic_revision_conflict_rejection`: PASSED
       - `test_permission_enforcement_matrix`: PASSED
       - `test_normalized_queue_and_dual_write_transaction`: PASSED
       - `test_collaborative_queue_voting_and_dynamic_resort`: PASSED
       - `test_next_track_advancement`: PASSED
       - `test_moderator_privilege_escalation_prevention`: PASSED
       - `test_cross_room_queue_voting_and_manipulation_rejected`: PASSED
       - `test_legacy_queue_endpoint_preserves_normalized_records_and_votes`: PASSED
       - `test_rpc_fallback_error_classification_unit`: PASSED
       - `test_voting_on_played_item_rejected`: PASSED
       - `test_next_track_atomic_state_and_concurrency_conflict`: PASSED
       - `test_legacy_queue_reconciliation_database_error_handling`: PASSED
       - `test_rpc_concurrency_error_does_not_execute_fallback_writes`: PASSED
     - Real-Time Vault Listener & Ingestion:
       - `test_is_audio_message_detection`: PASSED
       - `test_checkpoint_persistence_and_advancement`: PASSED
       - `test_idempotent_ingestion_and_duplicate_suppression`: PASSED
       - `test_listener_registration_and_clean_shutdown`: PASSED
       - `test_unsupported_message_advances_checkpoint`: PASSED
     - Contiguous Watermark & Crash Resilience Regressions:
       - `test_out_of_order_live_message_does_not_advance_checkpoint`: PASSED
       - `test_process_interruption_preserves_lower_checkpoint`: PASSED
       - `test_ingestion_failure_halts_checkpoint`: PASSED
       - `test_duplicate_concurrency_idempotency`: PASSED
       - `test_restart_recovery_after_interruption`: PASSED

2. **Frontend Production Build**:
   - `npm --prefix frontend run build`: **Compiled successfully with code 0**
   - 10/10 static and dynamic routes generated without type errors or compilation warnings.

3. **Live Streaming Verification (All 4 Supabase Vault Tracks)**:
   - Msg 2 (`8b4c99b2-e1ee-4369-8169-96b330bc085c`, MONTAGEM YUESHI): 2,936,270 bytes M4A — Range 0-1023 -> 206 OK, Mid-seek -> 206 OK
   - Msg 3 (`39f8df39-b44f-4aec-aa50-257599a6057e`, Midu Echoing): 3,998,809 bytes M4A — Range 0-1023 -> 206 OK, Mid-seek -> 206 OK
   - Msg 4 (`b7aedc3d-fe3f-4835-b363-d27f0a9c3569`, Genesis): 8,422,443 bytes M4A — Range 0-1023 -> 206 OK, Mid-seek -> 206 OK
   - Msg 5 (`5127721c-1f2e-4442-a616-32be829a39b9`, Poison Tree): 7,823,749 bytes M4A — Range 0-1023 -> 206 OK, Mid-seek -> 206 OK
   - All 4 tracks verified with `ftypM4A` audio container signatures; synthetic 440Hz and 1028-byte dummy generation permanently eradicated.

4. **Live Upload Verification**:
   - `POST /api/uploads/file` verified end-to-end:
     - Sent to Telegram Vault via Telethon
     - Track registered in Supabase
     - Instant HTTP Range 206 stream verified
