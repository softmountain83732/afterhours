export interface UserProfile {
  id: string;
  telegram_id: number;
  telegram_user_id?: number;
  username: string;
  first_name?: string | null;
  last_name?: string | null;
  display_name: string;
  language_code?: string | null;
  is_bot?: boolean;
  bio?: string;
  avatar_url?: string | null;
  gender?: string | null;
  gender_public: boolean;
  is_guest: boolean;
  profile_completed?: boolean;
  uploads_count?: number;
  playlists_count?: number;
  favorites_count?: number;
  created_at: string;
}

export interface Track {
  id: string;
  title: string;
  artist_id?: string;
  artist_name: string;
  album_id?: string;
  album_title: string;
  artwork_url?: string;
  artwork_thumb_url?: string;
  duration_ms: number;
  track_number?: number | null;
  codec: string;
  bitrate?: number | null;
  file_hash: string;
  uploaded_by?: string | null;
  is_private: boolean;
  file_size?: number;
  is_favorite?: boolean;
  position?: number;
}

export interface Artist {
  id: string;
  name: string;
  image_url?: string;
}

export interface Album {
  id: string;
  title: string;
  artist_id: string;
  artwork_url?: string;
  artwork_thumb_url?: string;
  release_year?: number;
}

export interface Playlist {
  id: string;
  user_id: string;
  name: string;
  description?: string;
  is_public: boolean;
  track_count?: number;
  preview_artwork?: string | null;
  tracks?: Track[];
  is_owner?: boolean;
  created_at: string;
}

export interface RoomMember {
  user_id: string;
  username: string;
  display_name: string;
  avatar_url?: string | null;
  is_guest: boolean;
  role?: "host" | "dj" | "queue_manager" | "listener" | "moderator";
  can_control_playback: boolean;
  can_manage_queue: boolean;
  can_invite?: boolean;
  can_moderate?: boolean;
  joined_at: string;
}

export interface QueueItem {
  id: string;
  room_id: string;
  track_id: string;
  added_by: string;
  position: number;
  vote_count: number;
  played_at?: string | null;
  tracks?: Track;
  profiles?: {
    username: string;
    display_name: string;
  };
  created_at?: string;
}

export interface Room {
  id: string;
  name: string;
  host_id: string;
  host_name: string;
  is_public: boolean;
  is_active: boolean;
  invite_token?: string;
  listener_count: number;
  participant_count?: number;
  max_members: number;
  current_track_id?: string | null;
  current_track?: Track | null;
  playback_position_ms: number;
  is_playing: boolean;
  revision?: number;
  playback_rate?: number;
  server_timestamp?: string;
  scheduled_start_ms?: number | null;
  queue: string[];
  queue_tracks?: Track[];
  normalized_queue?: QueueItem[];
  members?: RoomMember[];
  user_permissions?: {
    is_host: boolean;
    role?: string;
    can_control_playback: boolean;
    can_manage_queue: boolean;
    can_invite?: boolean;
    can_moderate?: boolean;
  };
  created_at: string;
}

export interface ChatMessage {
  id: string;
  room_id: string;
  user_id: string;
  username: string;
  display_name: string;
  avatar_url?: string | null;
  content: string;
  created_at: string;
}

export type AudioQuality = "standard" | "high" | "saver";
