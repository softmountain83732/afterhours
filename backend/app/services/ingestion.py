"""Music ingestion service: metadata extraction, artwork extraction, optimization, and hashing."""

import hashlib
import io
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, APIC
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image


class IngestionService:
    """Handles parsing of music files, artwork optimization, and deduplication."""

    @staticmethod
    def compute_file_hash(file_path: Path) -> str:
        """Compute SHA-256 hash of entire file for exact duplicate detection."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def compute_image_hash(image_bytes: bytes) -> str:
        """Compute SHA-256 hash of image bytes."""
        return hashlib.sha256(image_bytes).hexdigest()

    @staticmethod
    def extract_metadata(file_path: Path) -> Dict[str, Any]:
        """
        Extract track metadata using Mutagen.
        Supports FLAC, MP3, M4A, OGG, WAV.
        """
        audio = mutagen.File(file_path)
        if audio is None:
            # Fallback for basic file info
            name_stem = file_path.stem
            return {
                "title": name_stem,
                "artist": "Unknown Artist",
                "album": "Unknown Album",
                "duration_ms": 0,
                "track_number": None,
                "codec": file_path.suffix.lstrip(".").lower(),
                "bitrate": None,
                "file_size": file_path.stat().st_size,
            }

        duration_ms = int(getattr(audio.info, "length", 0) * 1000)
        bitrate = getattr(audio.info, "bitrate", None)
        codec = file_path.suffix.lstrip(".").lower()

        title = None
        artist = None
        album = None
        track_number = None

        # Tags extraction depending on format
        tags = audio.tags if hasattr(audio, "tags") and audio.tags else audio

        if isinstance(audio, FLAC):
            codec = "flac"
            if audio.tags:
                title = audio.tags.get("title", [None])[0]
                artist = audio.tags.get("artist", [None])[0]
                album = audio.tags.get("album", [None])[0]
                tr_num = audio.tags.get("tracknumber", [None])[0]
                if tr_num:
                    try:
                        track_number = int(str(tr_num).split("/")[0])
                    except (ValueError, TypeError):
                        pass

        elif isinstance(tags, ID3) or hasattr(audio, "tags") and isinstance(audio.tags, ID3):
            id3 = audio.tags if hasattr(audio, "tags") and isinstance(audio.tags, ID3) else tags
            codec = "mp3"
            tit2 = id3.get("TIT2")
            if tit2:
                title = str(tit2)
            tpe1 = id3.get("TPE1")
            if tpe1:
                artist = str(tpe1)
            talb = id3.get("TALB")
            if talb:
                album = str(talb)
            trck = id3.get("TRCK")
            if trck:
                try:
                    track_number = int(str(trck).split("/")[0])
                except (ValueError, TypeError):
                    pass

        elif isinstance(audio, MP4):
            codec = "m4a"
            if audio.tags:
                title = audio.tags.get("\xa9nam", [None])[0]
                artist = audio.tags.get("\xa9ART", [None])[0]
                album = audio.tags.get("\xa9alb", [None])[0]
                trkn = audio.tags.get("trkn", [(None, None)])[0]
                if trkn and trkn[0]:
                    track_number = trkn[0]

        # General dictionary fallback
        if not title and tags:
            try:
                title = str(tags.get("title", [file_path.stem])[0])
            except Exception:
                title = file_path.stem

        if not artist and tags:
            try:
                artist = str(tags.get("artist", ["Unknown Artist"])[0])
            except Exception:
                artist = "Unknown Artist"

        if not album and tags:
            try:
                album = str(tags.get("album", ["Unknown Album"])[0])
            except Exception:
                album = "Unknown Album"

        return {
            "title": str(title or file_path.stem),
            "artist": str(artist or "Unknown Artist"),
            "album": str(album or "Unknown Album"),
            "duration_ms": duration_ms,
            "track_number": track_number,
            "codec": codec,
            "bitrate": bitrate,
            "file_size": file_path.stat().st_size,
        }

    @staticmethod
    def extract_artwork(file_path: Path) -> Optional[bytes]:
        """
        Extract embedded high-resolution album artwork bytes from audio file.
        Supports FLAC, MP3 (ID3v2.3/v2.4 and v2.2), M4A/MP4, OGG Vorbis, and Opus.
        """
        try:
            audio = mutagen.File(file_path)
            if audio is None:
                return None

            # 1. FLAC pictures
            if isinstance(audio, FLAC) and audio.pictures:
                for pic in audio.pictures:
                    if pic.type == 3:  # Front cover
                        return pic.data
                return audio.pictures[0].data

            # 2. Check METADATA_BLOCK_PICTURE (FLAC tags or OGG / Opus)
            tags = getattr(audio, "tags", None) or audio
            if tags and hasattr(tags, "get"):
                meta_pic = tags.get("metadata_block_picture") or tags.get("METADATA_BLOCK_PICTURE")
                if meta_pic:
                    try:
                        import base64
                        b64_str = meta_pic[0] if isinstance(meta_pic, list) else meta_pic
                        pic_data = base64.b64decode(b64_str)
                        parsed_pic = Picture(pic_data)
                        return parsed_pic.data
                    except Exception:
                        pass

            # 3. ID3 (MP3) - APIC (ID3v2.3+) and PIC (ID3v2.2)
            if tags:
                if hasattr(tags, "getall"):
                    # Check APIC
                    apic_frames = tags.getall("APIC")
                    for apic in apic_frames:
                        if getattr(apic, "type", None) == 3:  # Front cover
                            return apic.data
                    if apic_frames:
                        return apic_frames[0].data

                    # Check PIC (ID3v2.2)
                    pic_frames = tags.getall("PIC")
                    for p in pic_frames:
                        if getattr(p, "type", None) == 3:
                            return p.data
                    if pic_frames:
                        return pic_frames[0].data

                # In case keys are like 'APIC:' or 'APIC:Cover'
                for key in list(getattr(tags, "keys", lambda: [])()):
                    if key.startswith("APIC") or key.startswith("PIC"):
                        frame = tags[key]
                        if hasattr(frame, "data"):
                            return frame.data

            # 4. MP4 / M4A cover art
            if isinstance(audio, MP4) and audio.tags:
                covr = audio.tags.get("covr")
                if covr and len(covr) > 0:
                    covr_item = covr[0]
                    return bytes(covr_item) if not isinstance(covr_item, bytes) else covr_item

        except Exception:
            pass

        # 5. Fallback: FFmpeg video stream extraction (for audio files with attached pictures)
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_img:
                tmp_img_path = Path(tmp_img.name)
            try:
                cmd = [
                    "ffmpeg", "-y", "-i", str(file_path),
                    "-an", "-vcodec", "copy",
                    str(tmp_img_path)
                ]
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                if res.returncode == 0 and tmp_img_path.exists() and tmp_img_path.stat().st_size > 0:
                    img_data = tmp_img_path.read_bytes()
                    if len(img_data) > 0:
                        return img_data

                # Alternate extraction with frame decode if copy failed
                cmd_decode = [
                    "ffmpeg", "-y", "-i", str(file_path),
                    "-an", "-vframes", "1", "-f", "image2",
                    str(tmp_img_path)
                ]
                res2 = subprocess.run(cmd_decode, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                if res2.returncode == 0 and tmp_img_path.exists() and tmp_img_path.stat().st_size > 0:
                    img_data = tmp_img_path.read_bytes()
                    if len(img_data) > 0:
                        return img_data
            finally:
                tmp_img_path.unlink(missing_ok=True)
        except Exception:
            pass

        return None

    @staticmethod
    def optimize_artwork(image_bytes: bytes) -> Tuple[bytes, bytes]:
        """
        Produce high-res full image (max 800x800) and thumbnail (max 300x300) in WebP format.
        Handles CMYK, RGBA, P, LA, and grayscale modes gracefully.
        Returns: (full_webp_bytes, thumb_webp_bytes)
        """
        image = Image.open(io.BytesIO(image_bytes))
        if image.mode != "RGB":
            image = image.convert("RGB")

        # 1. Full cover: max 800x800
        full_img = image.copy()
        full_img.thumbnail((800, 800), Image.Resampling.LANCZOS)
        full_buf = io.BytesIO()
        full_img.save(full_buf, format="WEBP", quality=85, method=6)
        full_webp = full_buf.getvalue()

        # 2. Thumbnail: max 300x300
        thumb_img = image.copy()
        thumb_img.thumbnail((300, 300), Image.Resampling.LANCZOS)
        thumb_buf = io.BytesIO()
        thumb_img.save(thumb_buf, format="WEBP", quality=80, method=4)
        thumb_webp = thumb_buf.getvalue()

        return full_webp, thumb_webp
