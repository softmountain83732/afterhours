"""Main FastAPI application for Afterhours social music platform."""

import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import (
    auth,
    profiles,
    tracks,
    streaming,
    playlists,
    rooms,
    favorites,
    history,
    uploads,
)
from app.services.supabase_client import supabase_service
from app.services.telegram import telegram_service
from app.services.vault_listener import vault_listener_service

logger = logging.getLogger("main")

# Redact sensitive parameters from streaming access logs
class SensitiveLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = re.sub(r"([?&]token=)[^&\s]+", r"\1[REDACTED]", record.msg)
            record.msg = re.sub(r"([?&]tgWebAppData=)[^&\s]+", r"\1[REDACTED]", record.msg)
        return True

for log_name in ("uvicorn", "uvicorn.access", "main", "streaming_router"):
    logging.getLogger(log_name).addFilter(SensitiveLogFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    settings = get_settings()

    # Ensure required cache and session directories exist
    audio_dir = Path(settings.AUDIO_CACHE_DIR)
    artwork_dir = Path(settings.ARTWORK_CACHE_DIR)
    audio_dir.mkdir(parents=True, exist_ok=True)
    artwork_dir.mkdir(parents=True, exist_ok=True)
    Path("./sessions").mkdir(parents=True, exist_ok=True)

    logger.info(f"Resolved audio cache directory: {settings.AUDIO_CACHE_DIR}")
    logger.info(f"Resolved artwork cache directory: {settings.ARTWORK_CACHE_DIR}")

    # Supabase service diagnostic status at startup
    if supabase_service.is_live:
        logger.info(
            "Supabase service ready at startup: is_live=True (admin=%s, client=%s).",
            supabase_service.admin_client is not None,
            supabase_service.client is not None,
        )
    else:
        logger.warning(
            "Supabase service is NOT live at startup (reason: %s).",
            supabase_service.offline_reason,
        )

    # Verify Telegram Bot Token identity against Telegram Bot API (safe: logs only bot ID and username)
    if settings.TELEGRAM_BOT_TOKEN and not settings.TELEGRAM_BOT_TOKEN.startswith("placeholder"):
        try:
            from app.auth import verify_telegram_bot_identity
            bot_info = await verify_telegram_bot_identity(settings.TELEGRAM_BOT_TOKEN)
            if bot_info and bot_info.get("username"):
                settings.TELEGRAM_BOT_USERNAME = bot_info["username"]
        except Exception as e:
            logger.debug(f"Bot token startup verification check: {e}")

    # Initialize Telegram Telethon MTProto client and start Vault listener
    try:
        await telegram_service.initialize()
        if telegram_service.is_connected:
            await vault_listener_service.start()
            logger.info("Telegram Vault listener started successfully.")
        else:
            logger.warning(
                "Telegram service initialized but client is not connected/authorized; "
                "Vault auto-ingestion listener could not be started."
            )
    except Exception as e:
        logger.error(f"Error during Telegram listener startup: {e}", exc_info=True)

    yield

    # Cleanly stop Vault listener and disconnect Telegram client on shutdown
    try:
        await vault_listener_service.stop()
        logger.info("VaultListenerService stopped cleanly.")
    except Exception as e:
        logger.warning(f"Error stopping Vault listener: {e}")

    try:
        await telegram_service.disconnect()
        logger.info("TelegramService disconnected cleanly.")
    except Exception as e:
        logger.warning(f"Error disconnecting Telegram service: {e}")


settings = get_settings()

app = FastAPI(
    title="Afterhours API",
    description="Backend API for Afterhours Social Music Platform (Telegram Mini App)",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware for Telegram Mini App and Web
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Telegram Mini App runs inside sandboxed WebViews
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)

AFTERHOURS_DEFAULT_ARTWORK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 500" width="100%" height="100%">
  <defs>
    <radialGradient id="bg" cx="50%" cy="50%" r="70%">
      <stop offset="0%" stop-color="#1e1838"/>
      <stop offset="50%" stop-color="#120e24"/>
      <stop offset="100%" stop-color="#08070d"/>
    </radialGradient>
    <radialGradient id="glow" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#a855f7" stop-opacity="0.4"/>
      <stop offset="60%" stop-color="#6366f1" stop-opacity="0.15"/>
      <stop offset="100%" stop-color="#000000" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="discGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#2a2347"/>
      <stop offset="100%" stop-color="#110d1f"/>
    </linearGradient>
    <linearGradient id="accentGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#c084fc"/>
      <stop offset="50%" stop-color="#818cf8"/>
      <stop offset="100%" stop-color="#38bdf8"/>
    </linearGradient>
  </defs>
  <rect width="500" height="500" rx="36" fill="url(#bg)"/>
  <circle cx="250" cy="250" r="220" fill="url(#glow)"/>
  <circle cx="250" cy="250" r="180" fill="url(#discGrad)" stroke="#382d5e" stroke-width="2"/>
  <circle cx="250" cy="250" r="160" fill="none" stroke="#251d42" stroke-width="1.5" stroke-dasharray="8 4"/>
  <circle cx="250" cy="250" r="140" fill="none" stroke="#221a3c" stroke-width="1.5"/>
  <circle cx="250" cy="250" r="120" fill="none" stroke="#2a204d" stroke-width="1.5" stroke-dasharray="12 6"/>
  <circle cx="250" cy="250" r="100" fill="none" stroke="#221a3c" stroke-width="1.5"/>
  <circle cx="250" cy="250" r="75" fill="#140f26" stroke="url(#accentGrad)" stroke-width="3"/>
  <circle cx="250" cy="250" r="68" fill="none" stroke="#3b2b68" stroke-width="1"/>
  <g transform="translate(250, 250)">
    <circle cx="0" cy="0" r="12" fill="#090810" stroke="url(#accentGrad)" stroke-width="2"/>
    <path d="M-36 -8 L-36 8 M-24 -18 L-24 18 M-12 -28 L-12 28 M12 -28 L12 28 M24 -18 L24 18 M36 -8 L36 8" 
          stroke="url(#accentGrad)" stroke-width="3.5" stroke-linecap="round"/>
  </g>
  <text x="250" y="445" text-anchor="middle" fill="#94a3b8" font-family="system-ui, -apple-system, sans-serif" font-size="14" font-weight="700" letter-spacing="6">AFTERHOURS</text>
</svg>"""

DEFAULT_FALLBACK_ARTWORK_URL = "/api/static/artwork/default.svg"

# Resilient static artwork endpoint with Supabase Storage fetch & fallback
artwork_dir = Path(settings.ARTWORK_CACHE_DIR)
artwork_dir.mkdir(parents=True, exist_ok=True)


@app.get("/api/config")
async def get_public_config():
    """
    Public configuration endpoint exposing active bot username and environment.
    Never exposes credentials, API hashes, or tokens.
    """
    settings = get_settings()
    bot_username = (
        getattr(telegram_service, "bot_username", None)
        or settings.TELEGRAM_BOT_USERNAME
        or "afterhoursfmbot"
    )
    return {
        "bot_username": bot_username,
        "environment": settings.ENVIRONMENT,
    }


@app.get("/default-artwork.svg")
@app.get("/api/static/artwork/default.svg")
async def get_default_artwork():
    """Serve branded Afterhours default cover artwork with long-term cache."""
    return Response(
        content=AFTERHOURS_DEFAULT_ARTWORK_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@app.get("/api/static/artwork/{filename}")
async def get_static_artwork(filename: str):
    """
    Serve cached artwork, fetching from Supabase Storage on dyno restart or falling back to branded SVG.
    Content-addressed immutable files receive max-age=31536000, immutable.
    """
    if filename in ("default.svg", "fallback.svg", "default_artwork.svg"):
        return Response(
            content=AFTERHOURS_DEFAULT_ARTWORK_SVG,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=604800, immutable"},
        )

    # Determine media type
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    media_type_map = {
        "webp": "image/webp",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "svg": "image/svg+xml",
    }
    media_type = media_type_map.get(ext, "image/webp")

    # Content-addressed artwork files (UUIDs or hashes) are immutable
    is_content_addressed = len(filename) >= 20 and ext in ("webp", "png", "jpg", "jpeg")
    cache_headers = (
        {"Cache-Control": "public, max-age=31536000, immutable"}
        if is_content_addressed
        else {"Cache-Control": "public, max-age=86400"}
    )

    cached_path = artwork_dir / filename
    if cached_path.exists():
        return FileResponse(str(cached_path), media_type=media_type, headers=cache_headers)

    # If missing on disk (e.g. after Heroku dyno restart), check Supabase Storage
    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        if admin:
            try:
                data = admin.storage.from_("artwork").download(filename)
                if data:
                    cached_path.parent.mkdir(parents=True, exist_ok=True)
                    cached_path.write_bytes(data)
                    return FileResponse(str(cached_path), media_type=media_type, headers=cache_headers)
            except Exception:
                # File not found in Supabase Storage: fall through to brand-consistent SVG cover
                pass

    # Never return 404 or broken external redirects: serve brand-consistent SVG cover
    return Response(
        content=AFTERHOURS_DEFAULT_ARTWORK_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )

# Mount Routers
app.include_router(auth.router)
app.include_router(profiles.router)
app.include_router(tracks.router)
app.include_router(streaming.router)
app.include_router(playlists.router)
app.include_router(rooms.router)
app.include_router(favorites.router)
app.include_router(history.router)
app.include_router(uploads.router)


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "afterhours-backend",
        "version": "1.0.0",
        "environment": settings.ENVIRONMENT,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
