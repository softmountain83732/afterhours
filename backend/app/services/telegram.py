"""Telegram Vault integration using Telethon for MTProto access."""

import asyncio
import os
from pathlib import Path
from typing import Optional, Dict, Any
from telethon import TelegramClient
from telethon.tl.types import MessageMediaDocument, DocumentAttributeAudio

from app.config import get_settings


class TelegramService:
    """Manages Telethon client and MTProto operations for Afterhours Vault."""

    def __init__(self):
        self.settings = get_settings()
        self.client: Optional[TelegramClient] = None
        self._is_connected = False
        self._lock = asyncio.Lock()

    async def initialize(self):
        """Initialize and connect Telethon client if real credentials are provided."""
        if (
            self.settings.TELEGRAM_API_ID == 1234567
            or not self.settings.TELEGRAM_API_HASH
            or self.settings.TELEGRAM_API_HASH.startswith("placeholder")
        ):
            # Development / mock mode
            self._is_connected = False
            return

        async with self._lock:
            try:
                session_dir = Path("./sessions")
                session_dir.mkdir(parents=True, exist_ok=True)
                session_file = session_dir / "afterhours_bot.session"

                self.client = TelegramClient(
                    str(session_file),
                    self.settings.TELEGRAM_API_ID,
                    self.settings.TELEGRAM_API_HASH
                )
                await self.client.start(bot_token=self.settings.TELEGRAM_BOT_TOKEN)
                self._is_connected = True
            except Exception as e:
                self._is_connected = False

    async def disconnect(self):
        """Disconnect Telethon client."""
        if self.client and self._is_connected:
            await self.client.disconnect()
            self._is_connected = False

    @property
    def is_connected(self) -> bool:
        """Check if Telethon client is connected."""
        return self._is_connected and self.client is not None

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

        # Try standard iter_messages first (works for user accounts)
        use_id_chunking = False
        count = 0
        try:
            async for message in self.client.iter_messages(chat_id, limit=limit, offset_id=offset_id):
                if is_audio_message(message):
                    yield message
                    count += 1
                    if limit and count >= limit:
                        return
        except Exception as e:
            # If bot access is restricted from GetHistoryRequest, fallback to ID chunking
            use_id_chunking = True

        if use_id_chunking:
            # Bot method: probe message IDs in batches starting from offset_id or 1
            current_id = max(1, offset_id)
            batch_size = 50
            empty_streak = 0
            max_empty_streak = 3  # stop if 3 consecutive chunks return zero messages

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
                await asyncio.sleep(0.1)  # Telegram rate limit prevention

    async def download_file_from_vault(
        self,
        chat_id: int,
        message_id: int,
        destination_path: Path
    ) -> Optional[Path]:
        """
        Download audio file from Telegram Vault channel message.
        Returns destination_path on success, or None on failure.
        """
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        if not self._is_connected or not self.client:
            # If in dev/mock mode and file already exists at path or sample file exists
            if destination_path.exists():
                return destination_path
            return None

        try:
            message = await self.client.get_messages(chat_id, ids=message_id)
            if not message or not message.media:
                return None

            downloaded = await self.client.download_media(message, file=str(destination_path))
            if downloaded and Path(downloaded).exists():
                return Path(downloaded)
            return None
        except Exception:
            return None

    async def send_to_vault(
        self,
        file_path: Path,
        caption: str = "",
        title: Optional[str] = None,
        artist: Optional[str] = None,
        duration: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Upload file to private Afterhours Vault channel.
        Returns dict with chat_id and message_id.
        """
        if not self._is_connected or not self.client:
            # Dev mock return
            return {
                "chat_id": self.settings.TELEGRAM_VAULT_CHAT_ID,
                "message_id": 999999,
                "is_mock": True
            }

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
                self.settings.TELEGRAM_VAULT_CHAT_ID,
                str(file_path),
                caption=caption,
                attributes=attributes,
                supports_streaming=True
            )
            return {
                "chat_id": self.settings.TELEGRAM_VAULT_CHAT_ID,
                "message_id": message.id
            }
        except Exception:
            return None


telegram_service = TelegramService()
