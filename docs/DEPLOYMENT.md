# Afterhours — Production VPS Deployment Guide

This guide covers deploying **Afterhours** onto a modest Linux VPS (Ubuntu 22.04 / 24.04 LTS recommended) using Docker Compose and NGINX.

---

## 1. Prerequisites

1. **VPS Specifications:**
   - 2 vCPU / 4 GB RAM minimum (transcoding FLAC with FFmpeg requires moderate CPU).
   - 40 GB+ SSD disk space (for local audio cache and Telethon sessions).
   - Ubuntu 22.04 LTS or newer.

2. **Domain & DNS:**
   - Domain name with DNS `A` records pointing to your VPS IP:
     - `afterhours.yourdomain.com` (Frontend & WebApp entry)
     - `api.afterhours.yourdomain.com` (or unified reverse proxy via NGINX)

3. **External Services & Credentials:**
   - **Supabase Project:**
     - Create project on [Supabase](https://supabase.com).
     - Obtain `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY`.
   - **Telegram Bot:**
     - Create bot via [@BotFather](https://t.me/BotFather) to get `TELEGRAM_BOT_TOKEN`.
     - Enable WebApp domain via BotFather `/setmenubutton` pointing to `https://afterhours.yourdomain.com`.
   - **Telegram MTProto App:**
     - Create application credentials on [my.telegram.org](https://my.telegram.org) to obtain `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`.
   - **Afterhours Vault Channel:**
     - Create a private Telegram Channel named **Afterhours Vault**.
     - Add your bot as an Administrator with full posting permissions.
     - Note the channel chat ID (usually negative integer e.g., `-1001234567890`).

---

## 2. Server Setup & Docker Installation

SSH into your VPS and install Docker & Docker Compose:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git ufw ffmpeg

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Install Docker Compose Plugin
sudo apt install -y docker-compose-plugin

# Setup UFW Firewall
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

---

## 3. Database Migration (Supabase)

1. Open your **Supabase Dashboard** -> **SQL Editor**.
2. Open `database/migrations/001_initial_schema.sql` from this repository.
3. Paste the entire SQL script and click **Run**.
4. Confirm all tables, indexes, triggers, and RLS policies are created:
   - `profiles`, `artists`, `albums`, `tracks`, `track_artwork`
   - `playlists`, `playlist_tracks`, `favorites`, `listening_history`
   - `rooms`, `room_members`, `room_messages`, `track_access`
   - `reports`, `user_blocks`, `upload_jobs`
5. Under **Storage**, create a public bucket named `artwork` and a private/public bucket named `avatars`.

---

## 4. Clone Repository & Configure Environment

```bash
git clone https://github.com/your-org/afterhours.git /opt/afterhours
cd /opt/afterhours

# Copy environment template
cp .env.example .env

# Edit environment variables
nano .env
```

Ensure the following variables are configured with your real keys:
```ini
SUPABASE_URL=https://xyz.supabase.co
SUPABASE_ANON_KEY=eyJhbGci...
SUPABASE_SERVICE_ROLE_KEY=eyJhbGci...

TELEGRAM_BOT_TOKEN=123456789:AA...
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef0123456789...
TELEGRAM_VAULT_CHAT_ID=-1001234567890

AUTH_SECRET=your-random-32-byte-hex-string
ENVIRONMENT=production

NEXT_PUBLIC_API_URL=https://afterhours.yourdomain.com
NEXT_PUBLIC_SUPABASE_URL=https://xyz.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGci...
```

---

## 5. SSL Certificate with Let's Encrypt

Obtain SSL certificates for your domain using Certbot:

```bash
sudo apt install -y certbot
sudo certbot certonly --standalone -d afterhours.yourdomain.com

# Copy or mount certificates into nginx/ssl directory
mkdir -p nginx/ssl
sudo cp /etc/letsencrypt/live/afterhours.yourdomain.com/fullchain.pem nginx/ssl/
sudo cp /etc/letsencrypt/live/afterhours.yourdomain.com/privkey.pem nginx/ssl/
```

---

## 6. Build and Launch Containers

```bash
docker compose build --no-cache
docker compose up -d
```

Verify service status:
```bash
docker compose ps
docker compose logs -f
```

Test health check:
```bash
curl http://localhost:8000/api/health
```

---

## 7. Operational Monitoring & Resource Limits

Monitor resource usage:
```bash
docker stats
```

Key operational parameters:
- **Audio Cache Size:** Managed automatically by LRU `FileCache` up to `MAX_CACHE_SIZE_MB` (default 5000MB).
- **Concurrency Limiting:** `MAX_CONCURRENT_STREAMS` (default 20) ensures FFmpeg transcoding does not overwhelm CPU cores.
- **Log Inspection:**
  ```bash
  docker compose logs -f backend
  docker compose logs -f frontend
  docker compose logs -f nginx
  ```

---

## 8. Backup and Recovery

1. **Database:** Supabase automatically manages point-in-time PostgreSQL backups and replication.
2. **Music Vault:** Original music files remain indefinitely preserved in your private Telegram Vault channel.
3. **Telethon Session Files:**
   Back up the `telethon_sessions` volume periodically:
   ```bash
   docker run --rm -v afterhours_telethon_sessions:/volume -v $(pwd):/backup alpine tar -czf /backup/telethon_session_backup.tar.gz -C /volume .
   ```
