"""User profile management endpoints."""

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from pydantic import BaseModel, Field

from app.auth import get_current_user, get_optional_user
from app.db import db

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
    profile = db.profiles.get(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    # Username uniqueness validation
    desired_username = req.username.strip().lower()
    existing_owner = db.profiles_by_username.get(desired_username)
    if existing_owner and existing_owner != user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username is already taken"
        )

    # Remove old username mapping if changed
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
    user_id = db.profiles_by_username.get(username.lower())
    if not user_id or user_id not in db.profiles:
        raise HTTPException(status_code=404, detail="User not found")

    profile = db.profiles[user_id]
    is_owner = current_user and current_user.get("sub") == user_id

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
