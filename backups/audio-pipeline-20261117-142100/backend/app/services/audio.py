"""Audio processing and transcoding service using FFmpeg and Mutagen."""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

BROWSER_NATIVE_FORMATS = {"mp3", "aac", "m4a", "ogg", "wav", "webm"}


class AudioService:
    """Handles audio inspection and FFmpeg transcoding."""

    @staticmethod
    def is_browser_compatible(codec_or_format: str) -> bool:
        """Check if format is natively supported by modern mobile & desktop browsers."""
        if not codec_or_format:
            return False
        clean = codec_or_format.lower().replace(".", "").strip()
        # While desktop Chrome supports FLAC, Safari/iOS Telegram WebViews can be inconsistent with FLAC
        # AAC and MP3 are universally supported across Telegram WebApp on iOS, Android, and Desktop
        return clean in {"aac", "mp3", "m4a"}

    @staticmethod
    async def transcode_to_aac(
        input_path: Path,
        output_path: Path,
        bitrate: str = "256k"
    ) -> bool:
        """
        Transcode input audio file (e.g. FLAC, WAV, ALAC) to AAC M4A using FFmpeg.
        Returns True on success, False on failure.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_output = output_path.with_suffix(".tmp.m4a")

        # ffmpeg -y -i input -c:a aac -b:a 256k -movflags +faststart output
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(input_path),
            "-c:a", "aac",
            "-b:a", bitrate,
            "-movflags", "+faststart",
            "-vn",
            str(temp_output)
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0 and temp_output.exists() and temp_output.stat().st_size > 0:
                temp_output.replace(output_path)
                return True
            else:
                if temp_output.exists():
                    temp_output.unlink(missing_ok=True)
                return False
        except Exception as e:
            if temp_output.exists():
                temp_output.unlink(missing_ok=True)
            return False

    @staticmethod
    def get_quality_bitrate(quality: str) -> str:
        """Map quality string to bitrate."""
        q = quality.lower()
        if q == "high":
            return "320k"
        elif q == "saver":
            return "128k"
        return "256k"  # standard
