import { UserProfile, Track, Playlist, Room, ChatMessage, AudioQuality } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

let authToken: string | null = null;
let refreshPromise: Promise<string | null> | null = null;

export function setApiToken(token: string | null) {
  authToken = token;
  if (typeof window !== "undefined") {
    if (token) {
      localStorage.setItem("afterhours_token", token);
    } else {
      localStorage.removeItem("afterhours_token");
    }
  }
}

export function getApiToken(): string | null {
  if (authToken) return authToken;
  if (typeof window !== "undefined") {
    authToken = localStorage.getItem("afterhours_token");
  }
  return authToken;
}

async function refreshDevToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  // Never attempt dev auth in production
  if (!API_BASE.includes("localhost") && !API_BASE.includes("127.0.0.1")) {
    return null;
  }
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    try {
      const res = await fetch(`${API_BASE}/api/auth/dev`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        setApiToken(data.access_token);
        return data.access_token;
      }
    } catch {
      // fallback
    } finally {
      refreshPromise = null;
    }
    return null;
  })();
  return refreshPromise;
}

export class ApiError extends Error {
  status: number;
  data?: any;

  constructor(status: number, message: string, data?: any) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
    Object.setPrototypeOf(this, ApiError.prototype);
  }
}

interface RequestOptions extends RequestInit {
  _retried?: boolean;
}

async function request<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
  const token = getApiToken();
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> || {}),
  };

  if (token && !headers["Authorization"]) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  if (!(options.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers,
  });

  if (res.status === 401 && !options._retried && endpoint !== "/api/auth/dev" && endpoint !== "/api/auth/telegram") {
    setApiToken(null);
    const newToken = await refreshDevToken();
    if (newToken) {
      return request<T>(endpoint, { ...options, _retried: true });
    }
  }

  if (!res.ok) {
    let errorDetail = "API request failed";
    let errorJson: any = undefined;
    try {
      errorJson = await res.json();
      errorDetail = errorJson.detail || errorDetail;
    } catch {
      errorDetail = await res.text() || res.statusText;
    }
    throw new ApiError(res.status, errorDetail, errorJson);
  }

  return res.json();
}

// ─── AUTH ────────────────────────────────────────────────────────────────────
export const api = {
  auth: {
    dev: () =>
      request<{ access_token: string; is_new_user: boolean; profile: UserProfile }>(
        "/api/auth/dev",
        { method: "POST" }
      ),
    telegram: (initData: string) =>
      request<{ access_token: string; is_new_user: boolean; profile: UserProfile }>(
        "/api/auth/telegram",
        { method: "POST", body: JSON.stringify({ init_data: initData }) }
      ),
    guest: (displayName?: string) =>
      request<{ access_token: string; profile: UserProfile }>(
        "/api/auth/guest",
        { method: "POST", body: JSON.stringify({ display_name: displayName }) }
      ),
    me: () => request<UserProfile>("/api/auth/me"),
  },

  profiles: {
    setup: (data: { display_name: string; username: string; bio?: string; gender?: string; gender_public?: boolean; avatar_url?: string }) =>
      request<UserProfile>("/api/profiles/setup", { method: "POST", body: JSON.stringify(data) }),
    getMe: () => request<UserProfile>("/api/profiles/me"),
    update: (data: Partial<UserProfile>) =>
      request<UserProfile>("/api/profiles/me", { method: "PATCH", body: JSON.stringify(data) }),
    getPublic: (username: string) => request<UserProfile & { playlists: Playlist[] }>(`/api/profiles/${username}`),
  },

  tracks: {
    list: (query?: string, limit = 50, offset = 0) => {
      const q = query ? `&q=${encodeURIComponent(query)}` : "";
      return request<{ items: Track[]; total: number }>(`/api/tracks?limit=${limit}&offset=${offset}${q}`);
    },
    get: (id: string) => request<Track>(`/api/tracks/${id}`),
    myUploads: () => request<Track[]>("/api/tracks/my-uploads"),
    delete: (id: string) => request<{ message: string }>(`/api/tracks/${id}`, { method: "DELETE" }),
  },

  streaming: {
    getUrl: (trackId: string, quality: AudioQuality = "standard") => {
      const token = getApiToken();
      const tokenQuery = token ? `&token=${encodeURIComponent(token)}` : "";
      return `${API_BASE}/api/stream/${trackId}?quality=${quality}${tokenQuery}`;
    },
  },

  playlists: {
    list: () => request<Playlist[]>("/api/playlists"),
    create: (data: { name: string; description?: string; is_public?: boolean }) =>
      request<Playlist>("/api/playlists", { method: "POST", body: JSON.stringify(data) }),
    get: (id: string) => request<Playlist>(`/api/playlists/${id}`),
    update: (id: string, data: { name?: string; description?: string; is_public?: boolean }) =>
      request<Playlist>(`/api/playlists/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    delete: (id: string) => request<{ message: string }>(`/api/playlists/${id}`, { method: "DELETE" }),
    addTrack: (playlistId: string, trackId: string) =>
      request<{ message: string }>(`/api/playlists/${playlistId}/tracks`, {
        method: "POST",
        body: JSON.stringify({ track_id: trackId }),
      }),
    removeTrack: (playlistId: string, trackId: string) =>
      request<{ message: string }>(`/api/playlists/${playlistId}/tracks/${trackId}`, { method: "DELETE" }),
    reorder: (playlistId: string, trackIds: string[]) =>
      request<{ message: string }>(`/api/playlists/${playlistId}/tracks/reorder`, {
        method: "PATCH",
        body: JSON.stringify({ track_ids: trackIds }),
      }),
  },

  rooms: {
    list: (query?: string) => {
      const q = query ? `?q=${encodeURIComponent(query)}` : "";
      return request<Room[]>(`/api/rooms${q}`);
    },
    create: (data: { name: string; is_public?: boolean; max_members?: number }) =>
      request<Room>("/api/rooms", { method: "POST", body: JSON.stringify(data) }),
    get: (id: string, inviteToken?: string) => {
      const q = inviteToken ? `?invite_token=${encodeURIComponent(inviteToken)}` : "";
      return request<Room>(`/api/rooms/${id}${q}`);
    },
    join: (id: string, inviteToken?: string) => {
      const q = inviteToken ? `?invite_token=${encodeURIComponent(inviteToken)}` : "";
      return request<{ message: string }>(`/api/rooms/${id}/join${q}`, { method: "POST" });
    },
    leave: (id: string) => request<{ message: string }>(`/api/rooms/${id}/leave`, { method: "POST" }),
    syncClock: (clientEcho: number) =>
      request<{ client_echo: number; server_time_ms: number; server_iso: string }>(`/api/rooms/sync/clock?client_echo=${clientEcho}`),
    dispatchCommand: (roomId: string, action: string, payload: any = {}, expectedRevision?: number) =>
      request<{ success: boolean; action: string; revision: number; server_timestamp: string; scheduled_start_ms?: number; room: Room }>(
        `/api/rooms/${roomId}/command`,
        { method: "POST", body: JSON.stringify({ action, expected_revision: expectedRevision, payload }) }
      ),
    getNormalizedQueue: (id: string) => request<any[]>(`/api/rooms/${id}/queue`),
    updatePlayback: (id: string, data: { current_track_id?: string; playback_position_ms: number; is_playing: boolean }) =>
      request<{ message: string; room: Room }>(`/api/rooms/${id}/playback`, { method: "PATCH", body: JSON.stringify(data) }),
    updateQueue: (id: string, queue: string[]) =>
      request<{ message: string; queue: string[] }>(`/api/rooms/${id}/queue`, { method: "POST", body: JSON.stringify({ queue }) }),
    updateMemberPermissions: (roomId: string, userId: string, data: { can_control_playback: boolean; can_manage_queue: boolean }) =>
      request<{ message: string }>(`/api/rooms/${roomId}/members/${userId}/permissions`, { method: "PATCH", body: JSON.stringify(data) }),
    kickMember: (roomId: string, userId: string) =>
      request<{ message: string }>(`/api/rooms/${roomId}/members/${userId}`, { method: "DELETE" }),
    transferHost: (roomId: string, newHostId: string) =>
      request<{ message: string }>(`/api/rooms/${roomId}/transfer`, { method: "POST", body: JSON.stringify({ new_host_id: newHostId }) }),
    close: (id: string) => request<{ message: string }>(`/api/rooms/${id}`, { method: "DELETE" }),
    getMessages: (id: string) => request<ChatMessage[]>(`/api/rooms/${id}/messages`),
    sendMessage: (id: string, content: string) =>
      request<ChatMessage>(`/api/rooms/${id}/messages`, { method: "POST", body: JSON.stringify({ content }) }),
  },

  favorites: {
    list: () => request<Track[]>("/api/favorites"),
    add: (trackId: string) => request<{ message: string }>(`/api/favorites/${trackId}`, { method: "POST" }),
    remove: (trackId: string) => request<{ message: string }>(`/api/favorites/${trackId}`, { method: "DELETE" }),
  },

  history: {
    list: (limit = 50) => request<{ history_id: string; played_at: string; track: Track }[]>(`/api/history?limit=${limit}`),
    record: (trackId: string, durationListenedMs: number) =>
      request<{ message: string }>("/api/history", { method: "POST", body: JSON.stringify({ track_id: trackId, duration_listened_ms: durationListenedMs }) }),
  },

  uploads: {
    file: async (file: File, hasPermission: boolean) => {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("has_permission", hasPermission ? "true" : "false");
      return request<{ message: string; track: Track; is_duplicate: boolean }>("/api/uploads/file", {
        method: "POST",
        body: formData,
      });
    },
  },

  config: {
    get: () => request<{ bot_username: string; environment: string }>("/api/config"),
  },
};
