"""Database access layer supporting both live Supabase client and in-memory dev store."""

import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from app.services.supabase_client import supabase_service


class DatabaseStore:
    """Provides unified data operations with live Supabase and development fallback."""

    def __init__(self):
        # In-memory stores for development/testing
        self.profiles: Dict[str, Dict[str, Any]] = {}  # key: id
        self.profiles_by_username: Dict[str, str] = {}  # username -> id
        self.profiles_by_telegram: Dict[int, str] = {}  # tg_id -> id
        self.tracks: Dict[str, Dict[str, Any]] = {}
        self.artists: Dict[str, Dict[str, Any]] = {}
        self.albums: Dict[str, Dict[str, Any]] = {}
        self.playlists: Dict[str, Dict[str, Any]] = {}
        self.playlist_tracks: Dict[str, List[Dict[str, Any]]] = {}  # playlist_id -> list of tracks
        self.favorites: Dict[str, set] = {}  # user_id -> set of track_ids
        self.listening_history: List[Dict[str, Any]] = []
        self.rooms: Dict[str, Dict[str, Any]] = {}
        self.room_members: Dict[str, Dict[str, Dict[str, Any]]] = {}  # room_id -> {user_id -> member_info}
        self.room_messages: Dict[str, List[Dict[str, Any]]] = {}  # room_id -> list of msgs
        self.track_access: List[Dict[str, Any]] = []
        self.upload_jobs: Dict[str, Dict[str, Any]] = {}
        self.reports: List[Dict[str, Any]] = []
        self.user_blocks: List[Dict[str, Any]] = []

        self._seed_sample_data()

    def _seed_sample_data(self):
        """Seed high quality initial tracks and demo artist/album for immediate testing."""
        artist_id = str(uuid.uuid4())
        self.artists[artist_id] = {
            "id": artist_id,
            "name": "Midnight Syndicate",
            "image_url": "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=400&q=80",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        album_id = str(uuid.uuid4())
        self.albums[album_id] = {
            "id": album_id,
            "title": "Neon Dreams",
            "artist_id": artist_id,
            "artwork_url": "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=800&q=80",
            "artwork_thumb_url": "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=300&q=80",
            "release_year": 2024,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        sample_tracks = [
            ("After Dark", 215000, 1),
            ("Velvet Horizons", 184000, 2),
            ("Starlight Drive", 242000, 3),
            ("Cyber City Pulse", 198000, 4),
            ("Echoes of Midnight", 260000, 5),
        ]

        for title, dur, tr_num in sample_tracks:
            t_id = str(uuid.uuid4())
            self.tracks[t_id] = {
                "id": t_id,
                "title": title,
                "artist_id": artist_id,
                "artist_name": "Midnight Syndicate",
                "album_id": album_id,
                "album_title": "Neon Dreams",
                "artwork_url": "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=800&q=80",
                "artwork_thumb_url": "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=300&q=80",
                "duration_ms": dur,
                "track_number": tr_num,
                "codec": "flac",
                "bitrate": 1411000,
                "file_hash": f"hash_{title.lower().replace(' ', '_')}",
                "telegram_chat_id": -1001234567890,
                "telegram_message_id": 100 + tr_num,
                "uploaded_by": None,
                "is_private": False,
                "file_size": 25000000,
                "ingestion_status": "completed",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        # Seed a public demo room
        room_id = str(uuid.uuid4())
        first_track_id = list(self.tracks.keys())[0]
        self.rooms[room_id] = {
            "id": room_id,
            "name": "Global Chillout Lounge",
            "host_id": "system_host",
            "host_name": "Afterhours Official",
            "is_public": True,
            "is_active": True,
            "invite_token": "global-lounge",
            "max_members": 30,
            "current_track_id": first_track_id,
            "playback_position_ms": 15000,
            "is_playing": True,
            "playback_updated_at": datetime.now(timezone.utc).isoformat(),
            "queue": [t_id for t_id in self.tracks.keys()],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.room_members[room_id] = {}
        self.room_messages[room_id] = []


db = DatabaseStore()
