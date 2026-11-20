"""Audio processing, validation, and transcoding service using FFmpeg and Mutagen."""

import asyncio
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("audio_service")
BROWSER_NATIVE_FORMATS = {"mp3", "aac", "m4a", "ogg", "wav", "webm"}


class AudioService:
    """Handles audio inspection, format validation, and FFmpeg transcoding."""

    @staticmethod
    def is_browser_compatible(codec_or_format: str) -> bool:
        """Check if format is natively supported by modern mobile & desktop browsers."""
        if not codec_or_format:
            return False
        clean = codec_or_format.lower().replace(".", "").strip()
        return clean in {"aac", "mp3", "m4a"}

    @staticmethod
    def validate_audio_file(file_path: Path) -> bool:
        """
        Validate whether a file is a genuine, playable audio file using ffprobe.
        Rejects zero-byte files, corrupt files, truncated downloads, and synthetic 1028-byte fallbacks.
        """
        if not file_path or not file_path.exists() or not file_path.is_file():
            return False

        try:
            file_size = file_path.stat().st_size
        except OSError:
            return False

        # Reject empty, tiny, or known dummy sizes (< 10KB or exact 1028-byte RIFF fallback)
        if file_size < 10240 or file_size == 1028:
            return False

        # Quick magic header checks
        try:
            with open(file_path, "rb") as f:
                header = f.read(16)
                if header.startswith(b"RIFF") and file_size <= 4096:
                    # Invalid synthetic dummy header
                    return False
                # If FLAC, verify flac header
                if file_path.suffix.lower() == ".flac" and not header.startswith(b"fLaC"):
                    return False
        except Exception:
            return False

        # Deep validation via ffprobe
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type,codec_name,channels,sample_rate,duration:format=duration,format_name",
            "-of", "json",
            str(file_path)
        ]

        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5
            )
            if res.returncode != 0 or not res.stdout.strip():
                return False

            info = json.loads(res.stdout)
            streams = info.get("streams", [])
            if not streams:
                return False

            audio_stream = streams[0]
            if audio_stream.get("codec_type") != "audio":
                return False

            # Duration check
            duration_str = audio_stream.get("duration") or info.get("format", {}).get("duration")
            if duration_str:
                try:
                    duration = float(duration_str)
                    if duration <= 0:
                        return False
                except ValueError:
                    pass

            return True
        except Exception as e:
            logger.debug(f"Audio validation failed for {file_path.name}: {e}")
            return False

    @staticmethod
    async def transcode_to_aac(
        input_path: Path,
        output_path: Path,
        bitrate: str = "256k",
        source_codec: Optional[str] = None
    ) -> bool:
        """
        Transcode or remux input audio file to AAC M4A using FFmpeg with faststart.
        If source is already AAC/M4A, attempts fast remux (-c:a copy -movflags +faststart)
        which completes in ~50ms without CPU-intensive re-encoding.
        Validates output audio before committing.
        Returns True on success, False on failure.
        """
        if not input_path or not input_path.exists() or not input_path.is_file():
            logger.error(f"Cannot transcode: source file does not exist: {input_path}")
            return False

        if not AudioService.validate_audio_file(input_path):
            logger.error(
                f"Cannot transcode invalid or corrupt source file: {input_path} "
                f"(size: {input_path.stat().st_size if input_path.exists() else 0} bytes)"
            )
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)
        import uuid
        temp_output = output_path.with_name(f"{output_path.stem}.tmp_{uuid.uuid4().hex[:8]}.m4a")

        is_aac_source = False
        clean_codec = (source_codec or input_path.suffix.replace(".", "")).lower().strip()
        if clean_codec in ("aac", "m4a", "mp4"):
            is_aac_source = True

        # 1. Fast remux attempt if source is already AAC
        if is_aac_source:
            remux_cmd = [
                "ffmpeg",
                "-y",
                "-i", str(input_path),
                "-map", "0:a:0",
                "-c:a", "copy",
                "-movflags", "+faststart",
                "-vn",
                str(temp_output)
            ]
            try:
                remux_res = await asyncio.to_thread(
                    subprocess.run,
                    remux_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30
                )
                if (
                    remux_res.returncode == 0
                    and temp_output.exists()
                    and AudioService.validate_audio_file(temp_output)
                ):
                    temp_output.replace(output_path)
                    logger.info(f"Fast remux completed for {input_path.name} -> {output_path.name} in ~50ms")
                    return True
                else:
                    logger.info(f"Fast remux fell through for {input_path.name}, falling back to AAC transcode")
                    if temp_output.exists():
                        temp_output.unlink(missing_ok=True)
            except Exception as remux_err:
                logger.debug(f"Fast remux attempt exception: {remux_err}")
                if temp_output.exists():
                    temp_output.unlink(missing_ok=True)

        # 2. Standard AAC transcode
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(input_path),
            "-map", "0:a:0",
            "-c:a", "aac",
            "-b:a", bitrate,
            "-movflags", "+faststart",
            "-vn",
            str(temp_output)
        ]

        try:
            res = await asyncio.to_thread(
                subprocess.run,
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=180
            )

            if (
                res.returncode == 0
                and temp_output.exists()
                and AudioService.validate_audio_file(temp_output)
            ):
                temp_output.replace(output_path)
                logger.info(f"Successfully transcoded {input_path.name} to {output_path.name} ({output_path.stat().st_size} bytes)")
                return True
            else:
                err_text = res.stderr or ""
                logger.error(
                    f"FFmpeg transcoding failed for {input_path.name} -> {output_path.name}.\n"
                    f"Exit code: {res.returncode}\n"
                    f"Command: {' '.join(cmd)}\n"
                    f"Stderr:\n{err_text.strip()}"
                )
                if temp_output.exists():
                    temp_output.unlink(missing_ok=True)
                return False
        except subprocess.TimeoutExpired as e:
            logger.error(f"FFmpeg transcoding timed out after {e.timeout}s for {input_path.name}")
            if temp_output.exists():
                temp_output.unlink(missing_ok=True)
            return False
        except Exception as e:
            logger.error(
                f"Exception during transcoding {input_path.name} -> {output_path.name}: "
                f"type={type(e).__name__}, repr={repr(e)}",
                exc_info=True
            )
            if temp_output.exists():
                temp_output.unlink(missing_ok=True)
            return False
        finally:
            if temp_output.exists():
                temp_output.unlink(missing_ok=True)

    @staticmethod
    def get_quality_bitrate(quality: str) -> str:
        """Map quality string to bitrate."""
        if not isinstance(quality, str):
            return "256k"
        q = quality.lower().strip()
        if q == "high":
            return "320k"
        elif q == "saver":
            return "128k"
        return "256k"  # standard
