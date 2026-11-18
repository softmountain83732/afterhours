# Afterhours — Architecture Decisions

## AD-001: Monorepo Structure
- `frontend/` — Next.js 14 App Router, TypeScript, Tailwind CSS
- `backend/` — Python FastAPI with Telethon, Mutagen, FFmpeg
- `database/` — Supabase SQL migrations
- `nginx/` — Reverse proxy config
- `docker-compose.yml` — Orchestration

## AD-002: Authentication Flow
Telegram WebApp `initData` → FastAPI validates HMAC-SHA256 → Upserts user in Supabase via service role → Returns JWT for Supabase client auth. Using Supabase `signInWithPassword` with a derived deterministic password from Telegram ID + server secret (not exposed to client). This avoids unsupported custom auth providers.

## AD-003: Music Storage
Original files stay in Telegram Vault (private channel). Supabase DB stores metadata + Telegram message references. Supabase Storage stores artwork (WebP) and avatars. FastAPI streams audio on-demand via Telethon, transcoding FLAC→AAC with FFmpeg when needed.

## AD-004: Audio Streaming Strategy
Telethon downloads don't support byte-range access natively. Strategy: FastAPI downloads track to temp cache, transcodes if needed, serves with proper Range/206 support from local cache. LRU cache with configurable max size. Cache key = telegram_message_id + format.

## AD-005: Room Synchronization
Supabase Realtime Broadcast for playback state sync. Supabase Presence for member tracking. No Socket.IO. Audio streams are independent per-listener via FastAPI.

## AD-006: Database
Supabase PostgreSQL with RLS. Migrations in `database/migrations/`. No ORM — raw SQL migrations, SQLAlchemy Core for FastAPI queries.

## AD-007: No Redis Initially
Not needed for MVP. FastAPI in-memory caching + filesystem cache for transcoded audio. Can add Redis later for horizontal scaling.

## AD-008: Guest Access
Guests get a temporary Supabase session tied to validated Telegram identity. Limited permissions enforced via RLS + API checks.

## AD-009: Personal Upload Privacy
Uploads are private by default. Room-scoped access grants are time-limited DB records. Deleting upload cascades to revoke all access.

## AD-010: Frontend State Management
React Context + hooks for player state, auth, rooms. No Redux. Zustand only if Context proves insufficient.

## AD-011: Telethon Session Storage via StringSession
To prevent platform-dependent SQLite lock contention and schema unpacking errors (`ValueError: too many values to unpack (expected 5)`) under Python 3.13, Telethon MTProto sessions are stored as stateless `TELEGRAM_STRING_SESSION` environment variables. This enables seamless containerization and zero disk lock issues.

## AD-012: Desktop Development Authentication Endpoint
To allow frontend testing and development in standard desktop browsers outside Telegram's WebView sandbox without requiring hardcoded mocks, `POST /api/auth/dev` creates or returns a local development profile (`afterhours_dev`) restricted strictly to loopback addresses (`127.0.0.1`, `localhost`, `::1`) when `DEV_AUTH_ENABLED` is true.

## AD-013: Telegram Audio Streaming Pipeline & Cache Integrity
- Synthetic 440Hz sine wave generation and 1,028-byte dummy file fallbacks are permanently removed. All streaming endpoints serve verified media or raise explicit HTTP 502/503/504 errors.
- Canonical audio cache is resolved to absolute paths anchored to `PROJECT_ROOT / 'cache' / 'audio'` regardless of working directory.
- Cached files are verified with `AudioService.validate_audio_file` via `ffprobe` (checking existence, size >= 10KB, magic headers, stream presence, and duration > 0). Corrupt or dummy files are automatically purged.
- Concurrent downloads/transcodes are guarded by per-key `asyncio.Lock` registries.
- Telegram MTProto session includes self-healing logic for `AuthKeyDuplicatedError` / `AuthKeyUnregisteredError`, dynamically generating fresh bot sessions and safely persisting them to `.env`.
- An audio pre-caching CLI service (`precache_audio.py`) pre-warms the cache from Telegram Vault to eliminate live user latency and playback timeouts.

## AD-014: Real-Time Telegram Vault Ingestion & Incremental Checkpointing
- `VaultListenerService` hooks into Telethon `events.NewMessage` for the private Vault channel on startup and cleanly unregisters on shutdown.
- Dual-tier checkpoint persistence: Supabase `vault_sync_checkpoints` table with local file fallback (`cache/vault_checkpoints.json`).
- Incremental reconciliation runs on startup and periodically (every 300s) scanning forward from the checkpoint, avoiding full rescanning of large libraries.
- Media validation, Mutagen metadata parsing, WebP artwork extraction, and SHA-256 deduplication are unified across real-time events and offline recovery.
- **Contiguous Watermark Ingestion Architecture (`WatermarkTracker`):**
  - Checkpoint advancement strictly requires contiguous completion of all preceding message IDs.
  - A newer live message (e.g. ID 10) arriving during offline reconciliation (IDs 6–9) is buffered in `processed_ids` and *never* jumps the persisted checkpoint.
  - Process interruptions or crashes preserve the lower contiguous checkpoint, guaranteeing un-reconciled messages are fetched upon restart.
  - Ingestion failures in the middle of a batch halt the checkpoint immediately at the failed message ID so it is retried on subsequent cycles.
  - Real-time messages are dispatched through an ordered `asyncio.Queue` worker with graceful drain on service shutdown.

## AD-015: Bounded On-Demand LRU Audio Cache with SQLite Access Tracking & Stream Pinning
- Configurable audio cache limit `MAX_AUDIO_CACHE_MB` (default 5120MB, validated > 0).
- High-performance SQLite index (`cache/audio_cache_index.db`) with WAL mode records file paths, sizes, quality variants, access timestamps, and pin counts.
- When the cache exceeds capacity, eviction selects the least-recently-accessed unpinned files until the cache drops to <= 90% of maximum capacity.
- Active audio streams and in-flight transcode writes are protected via reference-counted pins (`pin_context`) to prevent eviction during playback.

## AD-016: Spotify Jam-Style Collaborative Listening Rooms & Atomic Queue Architecture
- **Host Authority & Custom Guest Permissions**: Host maintains full room ownership, role modification, host transfer, and room closure rights. Guests are granted specific capabilities (`can_control_playback`, `can_manage_queue`, `can_invite`, `can_moderate`). Moderators are strictly prevented from escalating privileges to host, modifying the host, or modifying other moderators.
- **Atomic Normalized Queue & Dual-Write Transactions (`004_room_queue_atomicity_rpc.sql`)**: Single source of truth is normalized table `room_queue` with dual-write synchronization to legacy `rooms.queue` array for backward compatibility. Four atomic stored procedures (`add_room_queue_item_atomic`, `vote_room_queue_item_atomic`, `remove_room_queue_item_atomic`, `next_room_track_atomic`) run with `SECURITY DEFINER`, strict `search_path = public, pg_temp`, execution restricted to `service_role`, row-level locking (`FOR UPDATE`), and monotonic revision verification.
- **NTP-Style Clock Offset Estimation & Four-Tier Drift Correction**:
  - `POST /api/rooms/sync-clock` samples RTT and server time to estimate client clock offset theta.
  - Coordinated scheduled future start with configurable lead time (500–1500ms, default 800ms).
  - Four drift tiers: `< 80ms` (no action), `80–500ms` (rate `0.98x–1.02x`), `500–1500ms` (rate `0.95x–1.05x`), `>= 1500ms` (hard seek).

## AD-017: Supabase Realtime Broadcast, React Drift Isolation & Telegram WebApp Deep Links
- **Supabase Realtime REST Broadcast (`broadcast_room_event`)**: Backend publishes low-latency events (`playback_sync`, `queue_updated`, `permissions_updated`, `host_transferred`) over Supabase Realtime channel `room:{room_id}` using REST broadcast endpoint. Polling is downgraded from 3000ms to a 15000ms slow keep-alive fallback active only when Realtime is disconnected.
- **React Drift Re-render Loop Isolation (CB-02)**: Storing `playbackRate` in a mutable ref (`playbackRateRef`) removes it from `checkDrift` and `useEffect` dependency arrays, eliminating the unmount loop that resets playback speed to 1.0 and re-triggers NTP calibration on micro-adjustments.
- **Buffered WebKit Metadata Seek (MP-03)**: `safeSeek` verifies `audio.readyState >= 1` before seeking, queueing a one-time `loadedmetadata` listener if audio metadata is not yet decoded. This eliminates silent seek drops to `00:00` on iOS Safari and Telegram WebView during track transitions.
- **Telegram /start Deep Links & Bot Menu Button**: Telegram bot registers a `/start` pattern handler returning direct `KeyboardButtonWebView` inline buttons for `room_<id>` and `track_<id>` parameters. Telethon `SetBotMenuButtonRequest` sets the bot's persistent bottom-left menu button to launch Afterhours. In the frontend, `TelegramDeepLinkHandler` intercepts `initDataUnsafe.start_param` on boot and navigates directly to the target room or library track. Room invite buttons share `https://t.me/${botUsername}?startapp=room_${roomId}` via `navigator.share` with clipboard fallback.
