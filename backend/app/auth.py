"""Authentication module for Telegram WebApp verification and Supabase session management."""

import hashlib
import hmac
import json
import logging
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

logger = logging.getLogger("auth")
security = HTTPBearer(auto_error=False)


def validate_telegram_init_data(init_data: str, bot_token: str, max_age_seconds: int = 300) -> Dict[str, Any]:
    """
    Validate Telegram WebApp initData string using official HMAC-SHA256 algorithm.
    Returns parsed user dict if valid, raises HTTPException otherwise.
    Strictly adheres to Telegram Mini App specification:
      - 5-minute (300s) default freshness limit to prevent replay attacks.
      - Strips whitespace/quotes from bot token.
      - Disallows duplicate query parameters to prevent parameter pollution.
      - Preserves exact raw parameter values (unquoted) for data-check-string.
      - Handles optional Bot API 7+/8+ 'signature' parameter without arbitrary exclusion.
      - Constant-time HMAC-SHA256 verification.
      - Never logs bot tokens, prefixes, lengths, or raw initData.
    """
    if not init_data or not isinstance(init_data, str) or not init_data.strip():
        logger.warning("Telegram auth validation failed: initData is empty or missing")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Telegram initData"
        )

    raw_init_data = init_data.strip()

    # If full URL or fragment was passed, extract query portion
    if "tgWebAppData=" in raw_init_data:
        extracted = None
        for chunk in raw_init_data.replace("#", "&").replace("?", "&").split("&"):
            if chunk.startswith("tgWebAppData="):
                extracted = urllib.parse.unquote(chunk[len("tgWebAppData="):])
                break
        if extracted:
            raw_init_data = extracted

    if raw_init_data.startswith("?") or raw_init_data.startswith("#"):
        raw_init_data = raw_init_data[1:]

    # Clean bot token of quotes/spaces
    clean_bot_token = (bot_token or "").strip().strip("'\"")

    # In development mode with placeholder token, allow mock data for testing
    settings = get_settings()
    if settings.ENVIRONMENT == "development" and raw_init_data.startswith("mock_"):
        parts = raw_init_data.split("_")
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
        pairs = urllib.parse.parse_qsl(raw_init_data, keep_blank_values=True)
        if not pairs:
            raise ValueError("No parameters found in initData")
    except Exception as e:
        logger.warning("Telegram auth validation failed: parse_qsl error: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse initData: {str(e)}"
        )

    # Check for duplicate parameter keys (prevent HTTP parameter pollution and ambiguous HMAC)
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        logger.warning("Telegram auth validation failed: duplicate parameters detected in initData")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate parameters in Telegram initData"
        )

    parsed_params = dict(pairs)

    received_hash = parsed_params.pop("hash", None)
    if not received_hash or not isinstance(received_hash, str) or not received_hash.strip():
        logger.warning("Telegram auth validation failed: missing 'hash' param.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing hash in Telegram initData"
        )
    received_hash = received_hash.strip().lower()

    # Telegram Bot API 7.0+/8.0+ includes optional third-party Ed25519 'signature' parameter.
    # In official Telegram HMAC validation using the bot token, 'signature' is excluded.
    received_signature = parsed_params.pop("signature", None)

    # Check auth_date
    auth_date_str = parsed_params.get("auth_date")
    if not auth_date_str:
        logger.warning("Telegram auth validation failed: missing 'auth_date' param")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing auth_date in Telegram initData"
        )

    try:
        auth_date = int(auth_date_str)
        current_time = int(time.time())
        age = current_time - auth_date
        if age > max_age_seconds:
            logger.warning(
                "Telegram auth validation failed: auth_date expired (age=%ds > max_age=%ds)",
                age,
                max_age_seconds,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Telegram initData has expired"
            )
        # Clock skew tolerance: allow maximum 60 seconds into the future
        if age < -60:
            logger.warning("Telegram auth validation failed: auth_date is in future (age=%ds)", age)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Telegram initData has invalid timestamp"
            )
    except ValueError:
        logger.warning("Telegram auth validation failed: invalid auth_date format (%s)", auth_date_str)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid auth_date format"
        )

    # Verify bot token is configured
    if not clean_bot_token or clean_bot_token.startswith("placeholder"):
        logger.error("Telegram bot token is not configured or placeholder. Auth cannot succeed.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Server Telegram bot token is not configured"
        )

    # Build data_check_string: alphabetically sorted key=value joined by \n
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed_params.items(), key=lambda item: item[0])
    )

    # Calculate HMAC-SHA256: secret_key = HMAC_SHA256("WebAppData", bot_token)
    secret_key = hmac.new(b"WebAppData", clean_bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    is_valid = hmac.compare_digest(calculated_hash, received_hash)

    # If signature parameter was present and official exclusion didn't match, check inclusion fallback
    if not is_valid and received_signature is not None:
        params_with_sig = dict(parsed_params)
        params_with_sig["signature"] = received_signature
        dcs_with_sig = "\n".join(
            f"{k}={v}" for k, v in sorted(params_with_sig.items(), key=lambda item: item[0])
        )
        alt_hash = hmac.new(secret_key, dcs_with_sig.encode("utf-8"), hashlib.sha256).hexdigest()
        if hmac.compare_digest(alt_hash, received_hash):
            is_valid = True
            calculated_hash = alt_hash

    if not is_valid:
        logger.warning(
            "Telegram HMAC signature mismatch for keys: %s",
            sorted(list(parsed_params.keys())),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram initData signature"
        )

    user_str = parsed_params.get("user")
    if not user_str:
        logger.warning("Telegram auth validation failed: missing 'user' JSON data")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing user data in Telegram initData"
        )

    try:
        user_data = json.loads(user_str)
        logger.info(
            "Telegram initData validated successfully for telegram_id=%s, username=%s (age=%ds)",
            user_data.get("id"),
            user_data.get("username"),
            age,
        )
        return user_data
    except Exception as e:
        logger.warning("Telegram auth validation failed: invalid user JSON: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid user JSON in initData: {str(e)}"
        )


async def verify_telegram_bot_identity(bot_token: str) -> Optional[Dict[str, Any]]:
    """
    Safely verify that TELEGRAM_BOT_TOKEN belongs to a valid Telegram bot via getMe.
    Logs ONLY the bot ID, username, and bot first_name. NEVER logs the token or credentials.
    """
    clean_token = (bot_token or "").strip().strip("'\"")
    if not clean_token or clean_token.startswith("placeholder"):
        logger.info("Telegram bot token is placeholder or empty; skipping Bot API getMe verification.")
        return None

    try:
        import asyncio
        import urllib.request
        import json

        def _do_get_me():
            url = f"https://api.telegram.org/bot{clean_token}/getMe"
            req = urllib.request.Request(url, headers={"User-Agent": "Afterhours-Backend/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
            return None

        data = await asyncio.to_thread(_do_get_me)
        if data and data.get("ok"):
            res = data.get("result", {})
            bot_id = res.get("id")
            username = res.get("username")
            first_name = res.get("first_name")
            logger.info("Telegram bot token verified via Bot API: @%s (Bot ID: %s, Name: %s)", username, bot_id, first_name)
            return {"id": bot_id, "username": username, "first_name": first_name}
        else:
            logger.warning("Telegram Bot API getMe verification returned non-OK response.")
            return None
    except Exception as e:
        err_msg = str(e)
        if clean_token in err_msg:
            err_msg = err_msg.replace(clean_token, "[REDACTED_BOT_TOKEN]")
        logger.warning("Telegram Bot API getMe verification failed: %s: %s", type(e).__name__, err_msg)
        return None


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
