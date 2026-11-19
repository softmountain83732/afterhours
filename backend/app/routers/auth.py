"""Authentication endpoints."""

import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.auth import (
    validate_telegram_init_data,
    derive_user_password,
    create_app_jwt,
    get_current_user,
)
from app.config import get_settings
from app.db import db
from app.services.supabase_client import supabase_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TelegramAuthRequest(BaseModel):
    init_data: str


class GuestAuthRequest(BaseModel):
    display_name: Optional[str] = "Guest"


@router.post("/dev")
async def dev_auth(request: Request):
    """
    Authenticate automatically for local development outside Telegram.
    Restricted to localhost / dev environments with DEV_AUTH_ENABLED.
    """
    settings = get_settings()
    if not settings.DEV_AUTH_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Development authentication is disabled."
        )

    # Restrict to localhost
    client_host = request.client.host if request.client else "unknown"
    allowed_hosts = {"127.0.0.1", "localhost", "::1", "testclient"}
    if client_host not in allowed_hosts:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dev auth is only permitted from localhost."
        )

    dev_tg_id = 999999999
    dev_username = "afterhours_dev"
    dev_display_name = "Afterhours Dev"

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        res = admin.table("profiles").select("*").eq("telegram_id", dev_tg_id).execute()
        if res.data:
            profile = res.data[0]
            user_id = profile["id"]
        else:
            # Create in auth.users first to satisfy foreign key
            dev_email = f"tg_{dev_tg_id}@afterhours.local"
            dev_pass = derive_user_password(dev_tg_id, settings.AUTH_SECRET)
            try:
                auth_user = admin.auth.admin.create_user({
                    "email": dev_email,
                    "password": dev_pass,
                    "email_confirm": True,
                    "user_metadata": {"telegram_id": dev_tg_id, "username": dev_username}
                })
                user_id = str(auth_user.user.id)
            except Exception:
                # If auth user exists, search
                users = admin.auth.admin.list_users()
                matching = [u for u in users if u.email == dev_email]
                if matching:
                    user_id = str(matching[0].id)
                else:
                    user_id = str(uuid.uuid4())

            profile = {
                "id": user_id,
                "telegram_id": dev_tg_id,
                "username": dev_username,
                "display_name": dev_display_name,
                "bio": "Local Development Profile",
                "avatar_url": None,
                "gender": None,
                "gender_public": False,
                "is_guest": False,
            }
            admin.table("profiles").insert(profile).execute()

        # Cache in memory
        db.profiles[user_id] = profile
        db.profiles_by_telegram[dev_tg_id] = user_id
        db.profiles_by_username[dev_username] = user_id
    else:
        user_id = db.profiles_by_telegram.get(dev_tg_id)
        if not user_id:
            user_id = str(uuid.uuid4())
            profile = {
                "id": user_id,
                "telegram_id": dev_tg_id,
                "username": dev_username,
                "display_name": dev_display_name,
                "bio": "Local Development Profile",
                "avatar_url": None,
                "gender": None,
                "gender_public": False,
                "is_guest": False,
                "profile_completed": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            db.profiles[user_id] = profile
            db.profiles_by_telegram[dev_tg_id] = user_id
            db.profiles_by_username[dev_username] = user_id
        else:
            profile = db.profiles[user_id]

    token = create_app_jwt(
        user_id=user_id,
        telegram_id=dev_tg_id,
        username=profile.get("username", dev_username),
        is_guest=False
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "is_new_user": False,
        "profile": profile
    }


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

    is_new_user = False

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        res = admin.table("profiles").select("*").eq("telegram_id", tg_id).execute()
        if res.data:
            profile = res.data[0]
            user_id = profile["id"]
            profile["profile_completed"] = bool(profile.get("bio") or profile.get("gender") is not None or profile.get("avatar_url"))
        else:
            is_new_user = True
            # Unique username
            base_username = username.lower()
            candidate = base_username
            suffix = 1
            while True:
                existing = admin.table("profiles").select("id").eq("username", candidate).execute()
                if not existing.data:
                    break
                candidate = f"{base_username}_{suffix}"
                suffix += 1
            username = candidate

            # Create auth.users
            email = f"tg_{tg_id}@afterhours.local"
            password = derive_user_password(tg_id, settings.AUTH_SECRET)
            try:
                auth_user = admin.auth.admin.create_user({
                    "email": email,
                    "password": password,
                    "email_confirm": True,
                    "user_metadata": {"telegram_id": tg_id, "username": username}
                })
                user_id = str(auth_user.user.id)
            except Exception:
                users = admin.auth.admin.list_users()
                matching = [u for u in users if u.email == email]
                if matching:
                    user_id = str(matching[0].id)
                else:
                    user_id = str(uuid.uuid4())

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
            }
            admin.table("profiles").insert(profile).execute()
            profile["profile_completed"] = False

        db.profiles[user_id] = profile
        db.profiles_by_telegram[tg_id] = user_id
        db.profiles_by_username[profile["username"]] = user_id
    else:
        user_id = db.profiles_by_telegram.get(tg_id)
        if not user_id:
            is_new_user = True
            user_id = str(uuid.uuid4())
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
                "profile_completed": False,
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
        "is_new_user": is_new_user or (profile.get("profile_completed") is False),
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

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        res = admin.table("profiles").select("*").eq("id", user_id).execute()
        if res.data:
            p = res.data[0]
            p["profile_completed"] = bool(p.get("bio") or p.get("gender") is not None or p.get("avatar_url"))
            return p

    profile = db.profiles.get(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    profile.setdefault("profile_completed", bool(profile.get("bio") or profile.get("gender") is not None))
    return profile
