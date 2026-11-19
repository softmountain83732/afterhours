"""Main FastAPI application for Afterhours social music platform."""

import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
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

DEFAULT_FALLBACK_ARTWORK_URL = "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=800&q=80"

# Resilient static artwork endpoint with Supabase Storage fetch & fallback redirect
artwork_dir = Path(settings.ARTWORK_CACHE_DIR)
artwork_dir.mkdir(parents=True, exist_ok=True)

@app.get("/api/static/artwork/{filename}")
async def get_static_artwork(filename: str):
    """
    Serve cached artwork, fetching from Supabase Storage on dyno restart or falling back to default cover.
    """
    cached_path = artwork_dir / filename
    if cached_path.exists():
        return FileResponse(str(cached_path))

    # If missing on disk (e.g. after Heroku dyno restart), check Supabase Storage
    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        if admin:
            try:
                data = admin.storage.from_("artwork").download(filename)
                if data:
                    cached_path.parent.mkdir(parents=True, exist_ok=True)
                    cached_path.write_bytes(data)
                    return FileResponse(str(cached_path))
            except Exception:
                try:
                    public_url = admin.storage.from_("artwork").get_public_url(filename)
                    return RedirectResponse(url=public_url, status_code=307)
                except Exception:
                    pass

    return RedirectResponse(url=DEFAULT_FALLBACK_ARTWORK_URL, status_code=307)

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
