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
