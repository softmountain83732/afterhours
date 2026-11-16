"""Main FastAPI application for Afterhours social music platform."""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
from app.services.telegram import telegram_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    settings = get_settings()

    # Ensure required cache and session directories exist
    Path(settings.AUDIO_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.ARTWORK_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    Path("./sessions").mkdir(parents=True, exist_ok=True)

    # Initialize Telegram Telethon MTProto client
    try:
        await telegram_service.initialize()
    except Exception:
        pass

    yield

    # Disconnect Telegram client on shutdown
    try:
        await telegram_service.disconnect()
    except Exception:
        pass


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

# Static file serving for cached artwork
artwork_dir = Path(settings.ARTWORK_CACHE_DIR)
artwork_dir.mkdir(parents=True, exist_ok=True)
app.mount("/api/static/artwork", StaticFiles(directory=str(artwork_dir)), name="artwork")

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
