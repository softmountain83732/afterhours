"""Authentication endpoints."""

import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import (
    validate_telegram_init_data,
    create_app_jwt,
    get_current_user
)
from app.config import get_settings
from app.db import db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TelegramAuthRequest(BaseModel):
    init_data: str


class GuestAuthRequest(BaseModel):
    display_name: Optional[str] = "Guest"


@router.post("/telegram")
async def telegram_auth(req: TelegramAuthRequest):
    """Validate Telegram WebApp initData and authenticate user."""
    settings = get_settings()
    tg_user = validate_telegram_init_data(req.init_data, settings.TELEGRAM_BOT_TOKEN)

    tg_id = tg_user.get("id")
    username = tg_user.get("username") or f"user_{tg_id}"
    first_name = tg_user.get("first_name", "")
    last_name = tg_user.get("last_name", "")
    display_name = f"{first_name} {last_name}".strip() or username

    # Check if user already exists
    user_id = db.profiles_by_telegram.get(tg_id)
    is_new_user = False

    if not user_id:
        is_new_user = True
        user_id = str(uuid.uuid4())
        # Ensure unique username
        base_username = username.lower()
        candidate = base_username
        suffix = 1
        while candidate in db.profiles_by_username:
            candidate = f"{base_username}_{suffix}"
            suffix += 1
        username = candidate

        profile = {
            "id": user_id,
            "telegram_id": tg_id,
            "username": username,
            "display_name": display_name,
            "bio": "",
            "avatar_url": None,
            "gender": None,
            "gender_public": False,
            "is_guest": False,
            "profile_completed": False,  # First time setup needed
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        db.profiles[user_id] = profile
        db.profiles_by_telegram[tg_id] = user_id
        db.profiles_by_username[username] = user_id
    else:
        profile = db.profiles[user_id]

    token = create_app_jwt(
        user_id=user_id,
        telegram_id=tg_id,
        username=profile["username"],
        is_guest=profile.get("is_guest", False)
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "is_new_user": is_new_user or not profile.get("profile_completed", True),
        "profile": profile
    }


@router.post("/guest")
async def guest_auth(req: GuestAuthRequest):
    """Create a temporary guest session."""
    user_id = str(uuid.uuid4())
    guest_num = len([p for p in db.profiles.values() if p.get("is_guest")]) + 1
    username = f"guest_{guest_num}"
    display_name = req.display_name or f"Guest {guest_num}"

    profile = {
        "id": user_id,
        "telegram_id": 0,
        "username": username,
        "display_name": display_name,
        "bio": "Guest Listener",
        "avatar_url": None,
        "gender": None,
        "gender_public": False,
        "is_guest": True,
        "profile_completed": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    db.profiles[user_id] = profile
    db.profiles_by_username[username] = user_id

    token = create_app_jwt(
        user_id=user_id,
        telegram_id=0,
        username=username,
        is_guest=True
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "is_new_user": False,
        "profile": profile
    }


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Get current authenticated user profile."""
    user_id = user["sub"]
    profile = db.profiles.get(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile
