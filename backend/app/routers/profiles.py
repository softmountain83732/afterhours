"""User profile management endpoints."""

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import get_current_user, get_optional_user
from app.db import db
from app.services.supabase_client import supabase_service

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ProfileSetupRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=50)
    username: str = Field(..., min_length=3, max_length=30)
    bio: Optional[str] = Field(None, max_length=200)
    gender: Optional[str] = None
    gender_public: bool = False
    avatar_url: Optional[str] = None


class ProfileUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=50)
    bio: Optional[str] = Field(None, max_length=200)
    gender: Optional[str] = None
    gender_public: Optional[bool] = None
    avatar_url: Optional[str] = None


@router.post("/setup")
async def setup_profile(req: ProfileSetupRequest, user: dict = Depends(get_current_user)):
    """Complete first-time profile setup."""
    user_id = user["sub"]
    desired_username = req.username.strip().lower()

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        # Check username uniqueness
        existing = admin.table("profiles").select("id").eq("username", desired_username).execute()
        if existing.data and existing.data[0]["id"] != user_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username is already taken"
            )

        update_data = {
            "display_name": req.display_name.strip(),
            "username": desired_username,
            "bio": req.bio.strip() if req.bio else "",
            "gender": req.gender,
            "gender_public": bool(req.gender_public),
        }
        if req.avatar_url:
            update_data["avatar_url"] = req.avatar_url

        up_res = admin.table("profiles").update(update_data).eq("id", user_id).execute()
        if not up_res.data:
            raise HTTPException(status_code=404, detail="Profile not found")
        profile = up_res.data[0]
        profile["profile_completed"] = True
        db.profiles[user_id] = profile
        db.profiles_by_username[desired_username] = user_id
        return profile

    profile = db.profiles.get(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    existing_owner = db.profiles_by_username.get(desired_username)
    if existing_owner and existing_owner != user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username is already taken"
        )

    old_username = profile.get("username")
    if old_username and old_username in db.profiles_by_username:
        del db.profiles_by_username[old_username]

    profile["display_name"] = req.display_name.strip()
    profile["username"] = desired_username
    profile["bio"] = req.bio.strip() if req.bio else ""
    profile["gender"] = req.gender
    profile["gender_public"] = bool(req.gender_public)
    if req.avatar_url:
        profile["avatar_url"] = req.avatar_url
    profile["profile_completed"] = True
    profile["updated_at"] = datetime.now(timezone.utc).isoformat()

    db.profiles_by_username[desired_username] = user_id
    db.profiles[user_id] = profile

    return profile


@router.get("/me")
async def get_my_profile(user: dict = Depends(get_current_user)):
    """Get current user's profile with detailed statistics."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        prof_res = admin.table("profiles").select("*").eq("id", user_id).execute()
        if not prof_res.data:
            raise HTTPException(status_code=404, detail="Profile not found")
        profile = prof_res.data[0]

        # Statistics
        up_res = admin.table("tracks").select("id", count="exact").eq("uploaded_by", user_id).execute()
        uploads_count = up_res.count or len(up_res.data or [])

        pl_res = admin.table("playlists").select("id", count="exact").eq("user_id", user_id).execute()
        playlists_count = pl_res.count or len(pl_res.data or [])

        fav_res = admin.table("favorites").select("id", count="exact").eq("user_id", user_id).execute()
        favorites_count = fav_res.count or len(fav_res.data or [])

        return {
            **profile,
            "profile_completed": bool(profile.get("bio") or profile.get("gender") is not None or profile.get("avatar_url")),
            "uploads_count": uploads_count,
            "playlists_count": playlists_count,
            "favorites_count": favorites_count,
        }

    profile = db.profiles.get(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    my_uploads_count = sum(1 for t in db.tracks.values() if t.get("uploaded_by") == user_id)
    my_playlists_count = sum(1 for p in db.playlists.values() if p.get("user_id") == user_id)
    my_favorites_count = len(db.favorites.get(user_id, set()))

    return {
        **profile,
        "uploads_count": my_uploads_count,
        "playlists_count": my_playlists_count,
        "favorites_count": my_favorites_count,
    }


@router.patch("/me")
async def update_my_profile(req: ProfileUpdateRequest, user: dict = Depends(get_current_user)):
    """Update profile fields."""
    user_id = user["sub"]

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        update_data = {}
        if req.display_name is not None:
            update_data["display_name"] = req.display_name.strip()
        if req.bio is not None:
            update_data["bio"] = req.bio.strip()
        if req.gender is not None:
            update_data["gender"] = req.gender
        if req.gender_public is not None:
            update_data["gender_public"] = req.gender_public
        if req.avatar_url is not None:
            update_data["avatar_url"] = req.avatar_url

        if update_data:
            res = admin.table("profiles").update(update_data).eq("id", user_id).execute()
            if not res.data:
                raise HTTPException(status_code=404, detail="Profile not found")
            profile = res.data[0]
            db.profiles[user_id] = profile
            return profile

    profile = db.profiles.get(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if req.display_name is not None:
        profile["display_name"] = req.display_name.strip()
    if req.bio is not None:
        profile["bio"] = req.bio.strip()
    if req.gender is not None:
        profile["gender"] = req.gender
    if req.gender_public is not None:
        profile["gender_public"] = req.gender_public
    if req.avatar_url is not None:
        profile["avatar_url"] = req.avatar_url

    profile["updated_at"] = datetime.now(timezone.utc).isoformat()
    db.profiles[user_id] = profile

    return profile


@router.get("/{username}")
async def get_public_profile(username: str, current_user: Optional[dict] = Depends(get_optional_user)):
    """Get public profile by username."""
    current_user_id = current_user.get("sub") if current_user else None

    if supabase_service.is_live:
        admin = supabase_service.get_admin()
        res = admin.table("profiles").select("*").ilike("username", username.lower()).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="User not found")
        profile = res.data[0]
        user_id = profile["id"]
        is_owner = current_user_id == user_id

        pl_query = admin.table("playlists").select("*").eq("user_id", user_id)
        if not is_owner:
            pl_query = pl_query.eq("is_public", True)
        pl_res = pl_query.execute()

        return {
            "id": profile["id"],
            "username": profile["username"],
            "display_name": profile["display_name"],
            "bio": profile.get("bio", ""),
            "avatar_url": profile.get("avatar_url"),
            "gender": profile.get("gender") if (profile.get("gender_public") or is_owner) else None,
            "gender_public": profile.get("gender_public", False),
            "is_guest": profile.get("is_guest", False),
            "created_at": profile["created_at"],
            "playlists": pl_res.data or []
        }

    user_id = db.profiles_by_username.get(username.lower())
    if not user_id or user_id not in db.profiles:
        raise HTTPException(status_code=404, detail="User not found")

    profile = db.profiles[user_id]
    is_owner = current_user_id == user_id

    public_playlists = [
        p for p in db.playlists.values()
        if p.get("user_id") == user_id and (p.get("is_public") or is_owner)
    ]

    return {
        "id": profile["id"],
        "username": profile["username"],
        "display_name": profile["display_name"],
        "bio": profile.get("bio", ""),
        "avatar_url": profile.get("avatar_url"),
        "gender": profile.get("gender") if (profile.get("gender_public") or is_owner) else None,
        "gender_public": profile.get("gender_public", False),
        "is_guest": profile.get("is_guest", False),
        "created_at": profile["created_at"],
        "playlists": public_playlists
    }
