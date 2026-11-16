# Afterhours — Project Status

## Current Status: MVP Build Complete (Stages 1 — 8 Done)

## Completed Work

### Stage 1 — Foundation
- Repository initialized with clean, modular structure.
- Monorepo containing `frontend/` (Next.js 14, TypeScript, Tailwind), `backend/` (FastAPI, Telethon, Mutagen, FFmpeg), `database/` (PostgreSQL Supabase migrations), `nginx/` (reverse proxy), and root configs.
- Comprehensive database migration `001_initial_schema.sql` (663 lines) with all 16 tables, constraints, foreign keys, triggers, and Row Level Security policies.
- Architecture decisions logged in `DECISIONS.md`.

### Stage 2 — Authentication & User Profiles
- Secure server-side Telegram WebApp `initData` verification implementing HMAC-SHA256 data-check-string signature check.
- Deterministic session generation avoiding custom auth hacks.
- First-time profile creation with unique username enforcement and collision prevention.
- Privacy controls: gender is private by default with explicit toggle.
- Guest access flow with appropriate permission constraints.

### Stage 3 — Music Ingestion & Vault Architecture
- Mutagen metadata extraction (title, artist, album, duration, track number, format, bitrate).
- High-res embedded artwork extraction directly from original audio files.
- Artwork optimization into full (800x800) and thumbnail (300x300) WebP formats.
- SHA-256 exact file duplicate detection preventing redundant storage.
- Telethon MTProto client integration for the private **Afterhours Vault** Telegram channel.
- Personal upload flow requiring explicit confirmation that the uploader holds legal right or permission to share the audio.

### Stage 4 — Audio Playback & Streaming Engine
- Authorized streaming endpoint with HTTP Range / 206 Partial Content support for instantaneous seeking.
- Real FFmpeg audio transcoding to compatible AAC/M4A.
- Audio quality tiers: standard (256k), high (320k), saver (128k).
- Filesystem LRU audio and artwork cache.
- Concurrency limiting to prevent CPU exhaustion on modest VPS.

### Stage 5 — Personal Library & Playlists
- User playlists: create, rename, delete, add tracks, remove tracks, reorder tracks.
- Public vs. private playlists.
- User favorites system.
- Play event tracking and listening history.
- My Uploads management (view, play, add to playlist, delete with cascading access revocation).

### Stage 6 — Listening Rooms & Synchronization
- Global Rooms discovery (live indicator, listener count, host, current track preview, search, create).
- Private rooms protected by invitation tokens.
- Synchronized playback state (current track, playback position in ms, playing state, queue).
- Drift correction for smooth listener synchronization without constant audio reloads.
- Host permission delegation: `can_control_playback` and `can_manage_queue`.
- Room moderation: kick member, transfer host ownership, close room.
- Real-time room text chat.
- Room-scoped authorization for personal track sharing.

### Stage 7 — Integration & Testing
- Backend test suite with 9 passing integration tests covering all critical journeys.
- Next.js 14 production build compiled successfully with zero type errors.

### Stage 8 — Deployment Readiness
- Multi-stage Dockerfiles for frontend, backend, and NGINX reverse proxy.
- `docker-compose.yml` with health checks, network segregation, and persistent cache volumes.
- Production environment template `.env.example`.
- Complete VPS deployment guide `docs/DEPLOYMENT.md`.

---

## External Configuration Required from Owner
Before launching live in production, provide values in `.env`:
1. `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
2. `TELEGRAM_BOT_TOKEN` (from @BotFather)
3. `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` (from my.telegram.org)
4. `TELEGRAM_VAULT_CHAT_ID` (Private Telegram channel for music files)
5. `AUTH_SECRET` (32+ character random hex string)
