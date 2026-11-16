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
