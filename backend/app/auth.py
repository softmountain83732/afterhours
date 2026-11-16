"""Authentication module for Telegram WebApp verification and Supabase session management."""

import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Optional, Dict, Any
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
try:
    import jwt
    from jwt.exceptions import PyJWTError as JWTError
except ImportError:
    from jose import jwt, JWTError

from app.config import get_settings, Settings

security = HTTPBearer(auto_error=False)


def validate_telegram_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> Dict[str, Any]:
    """
    Validate Telegram WebApp initData string using official HMAC-SHA256 algorithm.
    Returns parsed user dict if valid, raises HTTPException otherwise.
    """
    if not init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Telegram initData"
        )

    # In development mode with placeholder token, allow mock data for testing
    settings = get_settings()
    if settings.ENVIRONMENT == "development" and init_data.startswith("mock_"):
        # Format: mock_123456_username_DisplayName
        parts = init_data.split("_")
        tg_id = int(parts[1]) if len(parts) > 1 else 999999
        username = parts[2] if len(parts) > 2 else "devuser"
        first_name = parts[3] if len(parts) > 3 else "Dev User"
        return {
            "id": tg_id,
            "username": username,
            "first_name": first_name,
            "is_mock": True
        }

    try:
        parsed_params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse initData: {str(e)}"
        )

    received_hash = parsed_params.pop("hash", None)
    if not received_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing hash in Telegram initData"
        )

    # Check auth_date
    auth_date_str = parsed_params.get("auth_date")
    if not auth_date_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing auth_date in Telegram initData"
        )

    try:
        auth_date = int(auth_date_str)
        current_time = int(time.time())
        if current_time - auth_date > max_age_seconds:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Telegram initData has expired"
            )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid auth_date format"
        )

    # Build data_check_string
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed_params.items(), key=lambda item: item[0])
    )

    # Calculate HMAC-SHA256
    # secret_key = HMAC_SHA256(bot_token, "WebAppData")
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram initData signature"
        )

    user_str = parsed_params.get("user")
    if not user_str:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing user data in Telegram initData"
        )

    try:
        user_data = json.loads(user_str)
        return user_data
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid user JSON in initData: {str(e)}"
        )


def derive_user_password(telegram_id: int, secret: str) -> str:
    """Derive deterministic password for Supabase user using HMAC-SHA256."""
    h = hmac.new(secret.encode("utf-8"), f"tg_{telegram_id}".encode("utf-8"), hashlib.sha256)
    return h.hexdigest() + "Aa1!"


def create_app_jwt(user_id: str, telegram_id: int, username: str, is_guest: bool = False) -> str:
    """Generate application JWT token."""
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": user_id,
        "telegram_id": telegram_id,
        "username": username,
        "is_guest": is_guest,
        "iat": now,
        "exp": now + (86400 * 7),  # 7 days
    }
    return jwt.encode(payload, settings.AUTH_SECRET, algorithm="HS256")


def decode_app_jwt(token: str) -> Dict[str, Any]:
    """Decode and validate application JWT token."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.AUTH_SECRET, algorithms=["HS256"])
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {str(e)}"
        )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security)
) -> Dict[str, Any]:
    """
    Dependency to get current authenticated user.
    Extracts Bearer token and verifies identity.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided"
        )
    token = credentials.credentials
    return decode_app_jwt(token)


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security)
) -> Optional[Dict[str, Any]]:
    """Optional user dependency (returns None if not authenticated)."""
    if not credentials:
        return None
    try:
        return decode_app_jwt(credentials.credentials)
    except Exception:
        return None
