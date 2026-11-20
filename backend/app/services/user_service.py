"""User registration and Telegram profile synchronization service.

Adapted from the HBTU identity synchronization pattern for Afterhours'
FastAPI + Supabase PostgreSQL architecture.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from app.auth import derive_user_password
from app.config import get_settings
from app.db import db
from app.services.supabase_client import supabase_service

logger = logging.getLogger("user_service")

# Concurrency lock for in-memory mock DB operations
_memory_lock = asyncio.Lock()


class UserService:
    """Handles automatic Telegram user registration, profile upserts, and sync."""

    @staticmethod
    def _extract_telegram_user(user: Any) -> Dict[str, Any]:
        """Extract and normalize fields from Telegram user object or dict."""
        if isinstance(user, dict):
            raw_id = user.get("id")
            username = user.get("username")
            first_name = user.get("first_name", "") or ""
            last_name = user.get("last_name", "") or ""
            language_code = user.get("language_code")
            is_bot = bool(user.get("is_bot", False))
        else:
            raw_id = getattr(user, "id", None)
            username = getattr(user, "username", None)
            first_name = getattr(user, "first_name", "") or ""
            last_name = getattr(user, "last_name", "") or ""
            language_code = getattr(user, "language_code", None)
            is_bot = bool(getattr(user, "is_bot", False))

        if not raw_id:
            raise ValueError("Telegram user object must contain a non-empty 'id'")

        try:
            telegram_id = int(raw_id)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid Telegram user ID: {raw_id}") from exc

        return {
            "telegram_id": telegram_id,
            "username": (username or "").strip() or None,
            "first_name": first_name.strip() or None,
            "last_name": last_name.strip() or None,
            "language_code": (language_code or "").strip() or None,
            "is_bot": is_bot,
        }

    async def register_or_sync_telegram_user(self, telegram_user: Any) -> Dict[str, Any]:
        """
        Upsert a Telegram user without replacing application-owned fields.

        - Extracts Telegram user identity.
        - Uses telegram_user_id (and telegram_id) as the unique external identity.
        - On first login: atomically creates auth.users + public.profiles record.
        - On subsequent logins: updates Telegram-owned fields (first_name, last_name,
          username, language_code, is_bot, updated_at) while strictly preserving
          application-owned fields (display_name, bio, avatar_url, gender, etc.).
        - Returns the persisted profile dictionary.
        """
        tg = self._extract_telegram_user(telegram_user)
        tg_id = tg["telegram_id"]
        settings = get_settings()
        now_iso = datetime.now(timezone.utc).isoformat()

        logger.info(
            "Syncing Telegram user: telegram_id=%s, username=%s, first_name=%s, language=%s",
            tg_id,
            tg["username"],
            tg["first_name"],
            tg["language_code"],
        )

        if supabase_service.is_live:
            return await self._sync_supabase(tg, settings, now_iso)
        else:
            return await self._sync_memory(tg, settings, now_iso)

    async def _sync_supabase(
        self, tg: Dict[str, Any], settings: Any, now_iso: str
    ) -> Dict[str, Any]:
        """Upsert Telegram user into live Supabase PostgreSQL database."""
        admin = supabase_service.get_admin()
        if not admin:
            raise RuntimeError("Database service unavailable: Supabase admin client not initialized.")

        tg_id = tg["telegram_id"]

        # 1. Look for existing profile by telegram_id or telegram_user_id
        try:
            res = (
                admin.table("profiles")
                .select("*")
                .or_(f"telegram_id.eq.{tg_id},telegram_user_id.eq.{tg_id}")
                .execute()
            )
        except Exception as query_err:
            # If telegram_user_id column is missing in older DB schema, fallback to telegram_id only
            logger.warning(
                "Query with telegram_user_id failed, falling back to telegram_id: %s",
                query_err,
            )
            try:
                res = admin.table("profiles").select("*").eq("telegram_id", tg_id).execute()
            except Exception as direct_err:
                logger.error("Failed to query profiles table in Supabase: %s", direct_err)
                raise direct_err

        existing_profiles = res.data or []

        # ─── CASE A: USER EXISTS → UPDATE TELEGRAM-OWNED FIELDS ─────────────
        if existing_profiles:
            existing = existing_profiles[0]
            user_id = existing["id"]

            # Compute new fields
            telegram_username = tg["username"] or existing.get("username")
            update_payload: Dict[str, Any] = {
                "telegram_id": tg_id,
                "telegram_user_id": tg_id,
                "first_name": tg["first_name"],
                "last_name": tg["last_name"],
                "language_code": tg["language_code"],
                "is_bot": tg["is_bot"],
                "updated_at": now_iso,
            }

            # Update username if Telegram has one and it doesn't conflict
            if tg["username"] and tg["username"] != existing.get("username"):
                try:
                    name_check = (
                        admin.table("profiles")
                        .select("id")
                        .eq("username", tg["username"])
                        .neq("id", user_id)
                        .execute()
                    )
                    if not name_check.data:
                        update_payload["username"] = tg["username"]
                except Exception as check_err:
                    logger.debug("Username uniqueness check error: %s", check_err)

            # Attempt update with full fields
            try:
                update_res = (
                    admin.table("profiles")
                    .update(update_payload)
                    .eq("id", user_id)
                    .execute()
                )
                updated_row = update_res.data[0] if update_res.data else {**existing, **update_payload}
            except Exception as upd_err:
                # If migration 006 columns don't exist yet in Supabase, strip them
                logger.warning(
                    "Update with full Telegram fields failed, retrying with baseline schema: %s",
                    upd_err,
                )
                safe_payload = {
                    "telegram_id": tg_id,
                    "updated_at": now_iso,
                }
                if "username" in update_payload:
                    safe_payload["username"] = update_payload["username"]
                admin.table("profiles").update(safe_payload).eq("id", user_id).execute()
                updated_row = {**existing, **safe_payload}

            # Ensure in-memory attributes reflect current sync
            merged_profile = {
                **existing,
                **updated_row,
                "telegram_user_id": tg_id,
                "first_name": tg["first_name"],
                "last_name": tg["last_name"],
                "language_code": tg["language_code"],
                "is_bot": tg["is_bot"],
            }
            if merged_profile.get("profile_completed") is None:
                merged_profile["profile_completed"] = True

            # Sync local cache
            db.profiles[user_id] = merged_profile
            db.profiles_by_telegram[tg_id] = user_id
            db.profiles_by_username[merged_profile["username"]] = user_id

            logger.info("Updated existing user profile: id=%s, tg_id=%s", user_id, tg_id)
            return merged_profile

        # ─── CASE B: NEW USER → ATOMIC INSERTION ─────────────────────────────
        # 1. Determine unique username
        base_username = (tg["username"] or f"user_{tg_id}").lower()
        candidate = base_username
        suffix = 1
        while True:
            try:
                chk = admin.table("profiles").select("id").eq("username", candidate).execute()
                if not chk.data:
                    break
            except Exception:
                break
            candidate = f"{base_username}_{suffix}"
            suffix += 1
        username = candidate

        # 2. Derive display name from Telegram first/last name
        name_parts = [p for p in [tg["first_name"], tg["last_name"]] if p]
        display_name = " ".join(name_parts) or tg["username"] or f"User {tg_id}"

        # 3. Create or find auth.users record
        email = f"tg_{tg_id}@afterhours.local"
        password = derive_user_password(tg_id, settings.AUTH_SECRET)
        try:
            auth_user = admin.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {
                    "telegram_id": tg_id,
                    "telegram_user_id": tg_id,
                    "username": username,
                },
            })
            user_id = str(auth_user.user.id)
        except Exception as auth_err:
            logger.debug("auth.admin.create_user encountered error/duplicate: %s", auth_err)
            try:
                users = admin.auth.admin.list_users()
                matching = [u for u in users if u.email == email]
                if matching:
                    user_id = str(matching[0].id)
                else:
                    user_id = str(uuid.uuid4())
            except Exception:
                user_id = str(uuid.uuid4())

        new_profile = {
            "id": user_id,
            "telegram_id": tg_id,
            "telegram_user_id": tg_id,
            "username": username,
            "first_name": tg["first_name"],
            "last_name": tg["last_name"],
            "display_name": display_name,
            "language_code": tg["language_code"],
            "is_bot": tg["is_bot"],
            "bio": "",
            "avatar_url": None,
            "gender": None,
            "gender_public": False,
            "is_guest": False,
            "profile_completed": True,
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        # 4. Atomic insert with concurrency handling
        try:
            insert_res = admin.table("profiles").insert(new_profile).execute()
            persisted = insert_res.data[0] if insert_res.data else new_profile
        except Exception as insert_err:
            err_msg = str(insert_err).lower()
            # If concurrent request already inserted this user, re-query and return
            if "duplicate key" in err_msg or "unique constraint" in err_msg:
                logger.info(
                    "Concurrent insert detected for telegram_id=%s; querying existing record.",
                    tg_id,
                )
                recheck = admin.table("profiles").select("*").eq("telegram_id", tg_id).execute()
                if recheck.data:
                    existing = recheck.data[0]
                    db.profiles[existing["id"]] = existing
                    return existing

            # If missing columns (e.g. migration 006 not applied yet), insert baseline schema
            logger.warning(
                "Full profiles insert failed; falling back to baseline schema: %s",
                insert_err,
            )
            baseline_profile = {
                "id": user_id,
                "telegram_id": tg_id,
                "username": username,
                "display_name": display_name,
                "bio": "",
                "avatar_url": None,
                "gender": None,
                "gender_public": False,
                "is_guest": False,
                "created_at": now_iso,
                "updated_at": now_iso,
            }
            try:
                insert_res = (
                    admin.table("profiles")
                    .insert({**baseline_profile, "profile_completed": True})
                    .execute()
                )
            except Exception:
                insert_res = admin.table("profiles").insert(baseline_profile).execute()

            persisted = insert_res.data[0] if (insert_res and insert_res.data) else new_profile

        # Synchronize local in-memory cache
        db.profiles[user_id] = {**new_profile, **persisted}
        db.profiles_by_telegram[tg_id] = user_id
        db.profiles_by_username[username] = user_id

        logger.info("Created new user profile: id=%s, tg_id=%s, username=%s", user_id, tg_id, username)
        return {**new_profile, **persisted}

    async def _sync_memory(
        self, tg: Dict[str, Any], settings: Any, now_iso: str
    ) -> Dict[str, Any]:
        """Upsert Telegram user into in-memory database (dev/test mode)."""
        async with _memory_lock:
            tg_id = tg["telegram_id"]
            user_id = db.profiles_by_telegram.get(tg_id)

            if user_id and user_id in db.profiles:
                existing = db.profiles[user_id]
                # Update Telegram-owned fields
                existing["telegram_id"] = tg_id
                existing["telegram_user_id"] = tg_id
                existing["first_name"] = tg["first_name"]
                existing["last_name"] = tg["last_name"]
                existing["language_code"] = tg["language_code"]
                existing["is_bot"] = tg["is_bot"]
                existing["updated_at"] = now_iso

                # Update username if Telegram provided one and not conflicting
                if tg["username"] and tg["username"] != existing.get("username"):
                    new_uname = tg["username"].lower()
                    if new_uname not in db.profiles_by_username or db.profiles_by_username[new_uname] == user_id:
                        old_uname = existing.get("username")
                        if old_uname in db.profiles_by_username:
                            del db.profiles_by_username[old_uname]
                        existing["username"] = new_uname
                        db.profiles_by_username[new_uname] = user_id

                logger.info("Updated in-memory user: id=%s, tg_id=%s", user_id, tg_id)
                return existing

            # Create new profile
            user_id = str(uuid.uuid4())
            base_username = (tg["username"] or f"user_{tg_id}").lower()
            candidate = base_username
            suffix = 1
            while candidate in db.profiles_by_username:
                candidate = f"{base_username}_{suffix}"
                suffix += 1
            username = candidate

            name_parts = [p for p in [tg["first_name"], tg["last_name"]] if p]
            display_name = " ".join(name_parts) or tg["username"] or f"User {tg_id}"

            new_profile = {
                "id": user_id,
                "telegram_id": tg_id,
                "telegram_user_id": tg_id,
                "username": username,
                "first_name": tg["first_name"],
                "last_name": tg["last_name"],
                "display_name": display_name,
                "language_code": tg["language_code"],
                "is_bot": tg["is_bot"],
                "bio": "",
                "avatar_url": None,
                "gender": None,
                "gender_public": False,
                "is_guest": False,
                "profile_completed": True,
                "created_at": now_iso,
                "updated_at": now_iso,
            }

            db.profiles[user_id] = new_profile
            db.profiles_by_telegram[tg_id] = user_id
            db.profiles_by_username[username] = user_id

            logger.info("Created in-memory user: id=%s, tg_id=%s, username=%s", user_id, tg_id, username)
            return new_profile


user_service = UserService()
