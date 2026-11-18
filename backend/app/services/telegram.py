"""Telegram Vault integration using Telethon StringSession for MTProto access."""

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, Callable
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaDocument, DocumentAttributeAudio
from telethon.errors import AuthKeyDuplicatedError, SecurityError
from telethon.errors.rpcerrorlist import AuthKeyUnregisteredError

from app.config import get_settings

logger = logging.getLogger("telegram_service")


def _persist_string_session(new_session: str) -> None:
    """Safely persist updated TELEGRAM_STRING_SESSION to .env files without altering other keys."""
    if not new_session:
        return

    # Check potential .env locations
    candidates = [
        Path(".env"),
        Path("backend/.env"),
        Path(__file__).resolve().parent.parent.parent / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    seen = set()
    for env_path in candidates:
        resolved = env_path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)

        if env_path.exists():
            try:
                text = env_path.read_text(encoding="utf-8")
                lines = text.splitlines()
                replaced = False
                new_lines = []
                for line in lines:
                    if line.startswith("TELEGRAM_STRING_SESSION="):
                        new_lines.append(f"TELEGRAM_STRING_SESSION={new_session}")
                        replaced = True
                    else:
                        new_lines.append(line)
                if not replaced:
                    new_lines.append(f"TELEGRAM_STRING_SESSION={new_session}")
                env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                logger.debug(f"Persisted updated string session to {env_path.name}")
            except Exception as e:
                logger.warning(f"Could not persist session to {env_path.name}: {e}")


SESSION_LOCK_FILE = Path(__file__).resolve().parent.parent.parent / "sessions" / "telegram_session.lock"


def _is_pid_alive(pid: int) -> bool:
    """Check if process with given PID is currently active."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _get_active_session_owner() -> Optional[int]:
    """Return PID of active session owner if lock file is valid and process is alive."""
    if SESSION_LOCK_FILE.exists():
        try:
            content = SESSION_LOCK_FILE.read_text(encoding="utf-8").strip()
            data = json.loads(content)
            pid = int(data.get("pid", 0))
            if pid and pid != os.getpid() and _is_pid_alive(pid):
                return pid
        except Exception:
            pass
    return None


def _acquire_session_lock(process_type: str = "backend"):
    """Record current process ownership of Telegram MTProto session."""
    try:
        SESSION_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "pid": os.getpid(),
            "timestamp": time.time(),
            "process": process_type
        }
        SESSION_LOCK_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _release_session_lock():
    """Release Telegram MTProto session ownership if owned by current process."""
    try:
        if SESSION_LOCK_FILE.exists():
            data = json.loads(SESSION_LOCK_FILE.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                SESSION_LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


class TelegramService:
    """Manages Telethon client and MTProto operations for Afterhours Vault."""

    def __init__(self):
        self.settings = get_settings()
        self.client: Optional[TelegramClient] = None
        self._is_connected = False
        self._lock = asyncio.Lock()
        self._bot_handlers_registered = False
        self.bot_username: Optional[str] = None

    async def initialize(self):
        """
        Initialize and connect Telethon client using StringSession.
        Includes automatic self-healing if existing session is invalidated by Telegram.
        """
        # Guard against placeholder / unconfigured credentials
        if (
            self.settings.TELEGRAM_API_ID == 1234567
            or not self.settings.TELEGRAM_API_HASH
            or self.settings.TELEGRAM_API_HASH.startswith("placeholder")
        ):
            self._is_connected = False
            return

        # Check for concurrent process ownership to prevent AuthKeyDuplicatedError
        owner_pid = _get_active_session_owner()
        if owner_pid:
            logger.warning(
                f"Telegram MTProto session is actively held by process PID {owner_pid}. "
                f"Skipping duplicate connection to avoid AuthKeyDuplicatedError."
            )
            return

        async with self._lock:
            # If already connected and authorized, reuse existing client
            if self._is_connected and self.client and self.client.is_connected():
                try:
                    if await self.client.is_user_authorized():
                        return
                except Exception:
                    pass

            # Close any previous half-open client before reconnecting
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
                self.client = None
            self._is_connected = False

            needs_fresh_session = False
            configured_session = self.settings.TELEGRAM_STRING_SESSION or ""

            # 1. Attempt connection with configured session
            try:
                session_obj = StringSession(configured_session)
            except Exception:
                session_obj = StringSession("")
                needs_fresh_session = True

            client = TelegramClient(
                session_obj,
                self.settings.TELEGRAM_API_ID,
                self.settings.TELEGRAM_API_HASH
            )

            try:
                await client.connect()
                if not await client.is_user_authorized():
                    # Session connected but not authorized; check if bot token can log in
                    if self.settings.TELEGRAM_BOT_TOKEN and not self.settings.TELEGRAM_BOT_TOKEN.startswith("placeholder"):
                        await client.start(bot_token=self.settings.TELEGRAM_BOT_TOKEN)
                    else:
                        needs_fresh_session = True
            except (AuthKeyDuplicatedError, AuthKeyUnregisteredError, SecurityError) as e:
                logger.warning(f"Configured Telegram session key was invalidated ({type(e).__name__}); recovering with fresh session.")
                needs_fresh_session = True
            except Exception as e:
                err_lower = str(e).lower()
                if "authorization key" in err_lower or "auth key" in err_lower or "session" in err_lower:
                    logger.warning(f"Telegram session connection error ({type(e).__name__}); recovering with fresh session.")
                    needs_fresh_session = True
                else:
                    logger.error(f"Failed to connect Telegram client: {type(e).__name__}: {e}")
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    return

            # 2. Self-healing recovery with fresh StringSession if needed
            if needs_fresh_session:
                try:
                    await client.disconnect()
                except Exception:
                    pass

                if not self.settings.TELEGRAM_BOT_TOKEN or self.settings.TELEGRAM_BOT_TOKEN.startswith("placeholder"):
                    logger.error("Cannot recover Telegram session: TELEGRAM_BOT_TOKEN is not configured.")
                    return

                logger.info("Initializing fresh Telethon session using bot token...")
                try:
                    client = TelegramClient(
                        StringSession(""),
                        self.settings.TELEGRAM_API_ID,
                        self.settings.TELEGRAM_API_HASH
                    )
                    await client.start(bot_token=self.settings.TELEGRAM_BOT_TOKEN)

                    # Persist the newly established session
                    new_session_str = client.session.save()
                    if new_session_str:
                        self.settings.TELEGRAM_STRING_SESSION = new_session_str
                        _persist_string_session(new_session_str)
                        logger.info("Fresh Telegram bot session established and safely persisted.")
                except Exception as e:
                    logger.error(f"Failed to start fresh Telegram bot session: {type(e).__name__}: {e}")
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    return

            # 3. Verify final authorization status
            try:
                is_authorized = await client.is_user_authorized() if client.is_connected() else False
            except Exception:
                is_authorized = False

            if client.is_connected() and is_authorized:
                self.client = client
                self._is_connected = True
                _acquire_session_lock("backend")
                try:
                    me = await client.get_me()
                    username = getattr(me, "username", "bot")
                    self.bot_username = username
                    if not self.settings.TELEGRAM_BOT_USERNAME:
                        self.settings.TELEGRAM_BOT_USERNAME = username
                    logger.info(f"Telegram client connected and authorized as @{username} (ID: {me.id})")
                except Exception:
                    logger.info("Telegram client connected and authorized.")

                # Configure WebApp Menu Button
                try:
                    from telethon.tl.types import InputUserEmpty, BotMenuButton
                    from telethon.tl.functions.bots import SetBotMenuButtonRequest
                    frontend_url = getattr(self.settings, "FRONTEND_URL", "http://localhost:3000").rstrip("/")
                    await client(SetBotMenuButtonRequest(
                        user_id=InputUserEmpty(),
                        button=BotMenuButton(text="Open Afterhours", url=frontend_url)
                    ))
                    logger.info(f"Telegram bot MenuButton configured to: {frontend_url}")
                except Exception as exc:
                    logger.debug(f"Could not set bot MenuButton: {exc}")

                # Register /start deep-link handler
                if not self._bot_handlers_registered:
                    try:
                        from telethon import events
                        from telethon.tl.types import KeyboardButtonWebView

                        async def handle_start(event):
                            raw_arg = event.pattern_match.group(1) or ""
                            arg = raw_arg.strip()
                            if arg.startswith("startapp="):
                                arg = arg[len("startapp="):].strip()
                            frontend_url = getattr(self.settings, "FRONTEND_URL", "http://localhost:3000").rstrip("/")
                            if arg.startswith("room_"):
                                r_id = arg[len("room_"):].strip()
                                app_url = f"{frontend_url}/room/{r_id}"
                                msg = "🎧 **You've been invited to a Listening Room!**\n\nTap below to join and listen in sync."
                                btn_text = "🎧 Join Listening Room"
                            elif arg.startswith("track_"):
                                t_id = arg[len("track_"):].strip()
                                app_url = f"{frontend_url}/library?track={t_id}"
                                msg = "🎵 **Listen on Afterhours**\n\nTap below to play this track."
                                btn_text = "🎵 Listen to Track"
                            else:
                                app_url = frontend_url
                                msg = "⚡ **Welcome to Afterhours!**\n\nHigh-fidelity collaborative audio streaming inside Telegram."
                                btn_text = "⚡ Launch Afterhours"
                            try:
                                await event.respond(msg, buttons=[[KeyboardButtonWebView(text=btn_text, url=app_url)]])
                            except Exception as e:
                                logger.error(f"Error replying to /start: {e}")

                        if hasattr(client, "add_event_handler"):
                            client.add_event_handler(handle_start, events.NewMessage(pattern=r"^/start(?:\s+(.*))?$"))
                        elif hasattr(client, "on"):
                            client.on(events.NewMessage(pattern=r"^/start(?:\s+(.*))?$"))(handle_start)
                        self._bot_handlers_registered = True
                        logger.info("Registered Telegram /start deep link command handler.")
                    except Exception as exc:
                        logger.debug(f"Could not register /start handler: {exc}")
            else:
                logger.error(f"Telegram client connected ({client.is_connected()}) but user is not authorized ({is_authorized}). Client not usable.")
                try:
                    await client.disconnect()
                except Exception:
                    pass
                self.client = None
                self._is_connected = False

    async def disconnect(self):
        """Disconnect Telethon client."""
        async with self._lock:
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
                self.client = None
            self._is_connected = False
            _release_session_lock()

    @property
    def is_connected(self) -> bool:
        """Check if Telethon client is actively connected AND authorized."""
        return self._is_connected and self.client is not None and self.client.is_connected()

    async def iter_audio_messages(
        self,
        chat_id: int,
        limit: Optional[int] = None,
        offset_id: int = 0
    ):
        """
        Iterate over messages in chat_id yielding messages containing audio files.
        Gracefully handles both user sessions (iter_messages) and bot sessions (get_messages by ID).
        """
        if not self.is_connected or not self.client:
            await self.initialize()
            if not self.is_connected or not self.client:
                return

        def is_audio_message(message) -> bool:
            if not message or not message.media:
                return False
            if hasattr(message, "audio") and message.audio:
                return True
            if hasattr(message, "document") and message.document:
                mime = getattr(message.document, "mime_type", "") or ""
                if mime.startswith("audio/"):
                    return True
                file_name = getattr(message, "file", None)
                name = getattr(file_name, "name", "") if file_name else ""
                if any(name.lower().endswith(ext) for ext in (".flac", ".mp3", ".m4a", ".aac", ".ogg", ".wav", ".aiff", ".opus")):
                    return True
            return False

        use_id_chunking = False
        count = 0
        try:
            async for message in self.client.iter_messages(chat_id, limit=limit, offset_id=offset_id):
                if is_audio_message(message):
                    yield message
                    count += 1
                    if limit and count >= limit:
                        return
        except Exception:
            use_id_chunking = True

        if use_id_chunking:
            current_id = max(1, offset_id)
            batch_size = 50
            empty_streak = 0
            max_empty_streak = 3

            while True:
                chunk_ids = list(range(current_id, current_id + batch_size))
                try:
                    messages = await self.client.get_messages(chat_id, ids=chunk_ids)
                except Exception:
                    break

                valid_messages = [m for m in messages if m]
                if not valid_messages:
                    empty_streak += 1
                    if empty_streak >= max_empty_streak:
                        break
                else:
                    empty_streak = 0

                for msg in valid_messages:
                    if is_audio_message(msg):
                        yield msg
                        count += 1
                        if limit and count >= limit:
                            return

                current_id += batch_size
                await asyncio.sleep(0.1)

    async def download_file_from_vault(
        self,
        chat_id: int,
        message_id: int,
        destination_path: Path,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Optional[Path]:
        """
        Download audio file from Telegram Vault channel message.
        Validates non-zero size, cleans up on error.
        Returns destination_path on success, or None on failure.
        """
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.is_connected or not self.client:
            await self.initialize()
            if not self.is_connected or not self.client:
                logger.error(f"Cannot download message {message_id}: Telegram client is not connected and authorized.")
                return None

        try:
            resolved_chat_id = int(chat_id)
            resolved_msg_id = int(message_id)
        except (ValueError, TypeError):
            logger.error(f"Invalid chat_id {chat_id} or message_id {message_id}")
            return None

        # Unique temp destination to avoid concurrent download file collisions
        temp_dest = destination_path.with_name(f"{destination_path.stem}.tmp_{uuid.uuid4().hex[:8]}{destination_path.suffix}")

        try:
            message = await self.client.get_messages(resolved_chat_id, ids=resolved_msg_id)
            if not message or not message.media:
                logger.error(f"Telegram message {resolved_msg_id} not found in chat {resolved_chat_id} or has no media")
                return None

            downloaded = None
            if hasattr(message, "document") and message.document:
                try:
                    with open(temp_dest, "wb") as f:
                        await self.client._download_file(
                            input_location=message.document,
                            file=f,
                            file_size=message.document.size,
                            part_size_kb=512,
                            progress_callback=progress_callback
                        )
                    downloaded = str(temp_dest)
                except Exception as dl_err:
                    logger.debug(f"Fast _download_file with 512KB parts fell back to standard download_media: {dl_err}")
                    if temp_dest.exists():
                        temp_dest.unlink(missing_ok=True)
                    downloaded = await self.client.download_media(
                        message,
                        file=str(temp_dest),
                        progress_callback=progress_callback
                    )
            else:
                downloaded = await self.client.download_media(
                    message,
                    file=str(temp_dest),
                    progress_callback=progress_callback
                )
            if downloaded and Path(downloaded).exists():
                actual_downloaded = Path(downloaded)
                file_size = actual_downloaded.stat().st_size
                if file_size > 0:
                    actual_downloaded.replace(destination_path)
                    logger.info(f"Successfully downloaded message {resolved_msg_id} from Vault: {file_size} bytes -> {destination_path.name}")
                    return destination_path

            if temp_dest.exists():
                temp_dest.unlink(missing_ok=True)
            return None
        except Exception as e:
            logger.error(f"Failed to download media for message {resolved_msg_id}: {type(e).__name__}: {e}")
            if temp_dest.exists():
                temp_dest.unlink(missing_ok=True)
            return None
        finally:
            if temp_dest.exists():
                temp_dest.unlink(missing_ok=True)

    async def send_to_vault(
        self,
        file_path: Path,
        caption: str = "",
        title: Optional[str] = None,
        artist: Optional[str] = None,
        duration: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Upload file directly to private Afterhours Vault channel.
        Returns dict with chat_id and message_id.
        """
        if not self.is_connected or not self.client:
            await self.initialize()
            if not self.is_connected or not self.client:
                raise RuntimeError("Telegram client is not connected and authorized")

        vault_chat_id = int(self.settings.TELEGRAM_VAULT_CHAT_ID)

        try:
            attributes = []
            if duration or title or artist:
                attributes.append(
                    DocumentAttributeAudio(
                        duration=int(duration or 0),
                        voice=False,
                        title=title or file_path.stem,
                        performer=artist or "Unknown Artist"
                    )
                )

            message = await self.client.send_file(
                vault_chat_id,
                str(file_path),
                caption=caption,
                attributes=attributes,
                supports_streaming=True
            )

            if not message or not message.id:
                raise RuntimeError("Telegram upload failed to return message ID")

            logger.info(f"Uploaded file {file_path.name} to Vault chat {vault_chat_id} as message {message.id}")
            return {
                "chat_id": vault_chat_id,
                "message_id": message.id
            }
        except Exception as e:
            logger.error(f"Failed to upload {file_path.name} to Telegram Vault: {type(e).__name__}: {e}")
            raise


telegram_service = TelegramService()
