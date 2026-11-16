"use client";

import React, { useEffect, useState, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  Users,
  Play,
  Pause,
  SkipForward,
  MessageSquare,
  Shield,
  Crown,
  Share2,
  LogOut,
  Send,
  UserX,
  ListMusic,
} from "lucide-react";
import { Room, ChatMessage, Track } from "@/lib/types";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/auth-context";
import { usePlayer } from "@/contexts/player-context";
import { useTelegram } from "@/contexts/telegram-context";

export default function RoomDetailPage() {
  const params = useParams();
  const roomId = params?.id as string;
  const router = useRouter();

  const { user } = useAuth();
  const { playTrack, togglePlay, isPlaying, seek, currentTrack, currentTime } = usePlayer();
  const { haptic } = useTelegram();

  const [room, setRoom] = useState<Room | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [newMessage, setNewMessage] = useState("");
  const [activeTab, setActiveTab] = useState<"chat" | "queue" | "members">("chat");
  const [isLoading, setIsLoading] = useState(true);

  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);

  const fetchRoomData = async () => {
    try {
      const data = await api.rooms.get(roomId);
      setRoom(data);

      // Playback synchronization / drift correction
      if (data.current_track) {
        if (!currentTrack || currentTrack.id !== data.current_track.id) {
          playTrack(data.current_track);
          if (data.playback_position_ms > 0) {
            seek(data.playback_position_ms / 1000);
          }
        } else {
          // Check drift
          const targetSeconds = data.playback_position_ms / 1000;
          const drift = Math.abs(currentTime - targetSeconds);
          if (drift > 2.0 && data.is_playing) {
            seek(targetSeconds);
          }
        }
      }

      // Sync messages
      const msgs = await api.rooms.getMessages(roomId);
      setMessages(msgs);
    } catch (err: any) {
      // Room not found or closed
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    // Join room on mount
    api.rooms.join(roomId).catch(() => {});
    fetchRoomData();

    // Polling sync (fallback to ensure continuous room state sync)
    pollIntervalRef.current = setInterval(fetchRoomData, 3000);

    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    };
  }, [roomId]);

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newMessage.trim()) return;

    haptic("light");
    const content = newMessage.trim();
    setNewMessage("");

    try {
      const sent = await api.rooms.sendMessage(roomId, content);
      setMessages((prev) => [...prev, sent]);
    } catch {
      // ignore
    }
  };

  const handleLeaveRoom = async () => {
    haptic("medium");
    try {
      await api.rooms.leave(roomId);
    } catch {}
    router.push("/room");
  };

  const handlePlaybackControl = async (playState: boolean) => {
    haptic("light");
    togglePlay();
    try {
      await api.rooms.updatePlayback(roomId, {
        playback_position_ms: Math.round(currentTime * 1000),
        is_playing: playState,
      });
    } catch {}
  };

  if (isLoading || !room) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] space-y-3">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        <span className="text-xs text-content-secondary">Joining listening room...</span>
      </div>
    );
  }

  const isHost = user?.id === room.host_id;
  const canControl = room.user_permissions?.can_control_playback || isHost;
  const canManageQueue = room.user_permissions?.can_manage_queue || isHost;

  return (
    <div className="flex flex-col space-y-4">
      {/* ─── ROOM HEADER ─────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between p-4 rounded-2xl bg-surface/90 border border-border">
        <div className="flex flex-col min-w-0 pr-2">
          <div className="flex items-center space-x-2">
            <h1 className="text-lg font-bold text-content-primary truncate">{room.name}</h1>
            <span className="px-2 py-0.5 rounded-full bg-primary/20 text-primary text-[10px] font-bold">
              {room.listener_count} Listening
            </span>
          </div>
          <span className="text-xs text-content-secondary mt-0.5">
            Host: <span className="text-content-primary font-medium">{room.host_name}</span>
          </span>
        </div>

        <div className="flex items-center space-x-2">
          <button
            onClick={() => {
              haptic("light");
              navigator.clipboard?.writeText(window.location.href);
              alert("Invite link copied to clipboard!");
            }}
            className="w-9 h-9 flex items-center justify-center rounded-full bg-white/5 text-content-secondary hover:text-content-primary"
            title="Share room"
          >
            <Share2 className="w-4 h-4" />
          </button>

          <button
            onClick={handleLeaveRoom}
            className="w-9 h-9 flex items-center justify-center rounded-full bg-red-500/10 text-red-400 hover:bg-red-500/20"
            title="Leave room"
          >
            <LogOut className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* ─── NOW PLAYING DISPLAY ─────────────────────────────────────────── */}
      <div className="p-4 rounded-2xl bg-surface-hover/80 border border-border flex items-center justify-between">
        <div className="flex items-center space-x-3 overflow-hidden flex-1 pr-3">
          <div className="relative w-12 h-12 flex-shrink-0 rounded-xl overflow-hidden bg-surface-light border border-border/40">
            <img
              src={room.current_track?.artwork_thumb_url || room.current_track?.artwork_url || "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=200&q=80"}
              alt=""
              className="w-full h-full object-cover"
            />
          </div>
          <div className="flex flex-col min-w-0">
            <span className="text-xs uppercase font-bold tracking-wider text-primary">Now Playing</span>
            <span className="text-sm font-bold text-content-primary truncate">
              {room.current_track?.title || "No track active"}
            </span>
            <span className="text-xs text-content-secondary truncate">
              {room.current_track?.artist_name || "Host can select a track from queue"}
            </span>
          </div>
        </div>

        {canControl && (
          <button
            onClick={() => handlePlaybackControl(!isPlaying)}
            className="w-11 h-11 flex items-center justify-center rounded-full bg-primary hover:bg-primary-hover text-white shadow-lg shadow-primary/30 transition-all active:scale-95"
          >
            {isPlaying ? <Pause className="w-5 h-5 fill-current" /> : <Play className="w-5 h-5 fill-current ml-0.5" />}
          </button>
        )}
      </div>

      {/* ─── TABS: CHAT, QUEUE, MEMBERS ───────────────────────────────────── */}
      <div className="flex items-center justify-around border-b border-border/60 pb-2">
        <button
          onClick={() => {
            haptic("light");
            setActiveTab("chat");
          }}
          className={`flex items-center space-x-1.5 py-1 px-3 text-xs font-semibold transition-colors ${
            activeTab === "chat" ? "text-primary border-b-2 border-primary" : "text-content-secondary"
          }`}
        >
          <MessageSquare className="w-4 h-4" />
          <span>Room Chat</span>
        </button>

        <button
          onClick={() => {
            haptic("light");
            setActiveTab("queue");
          }}
          className={`flex items-center space-x-1.5 py-1 px-3 text-xs font-semibold transition-colors ${
            activeTab === "queue" ? "text-primary border-b-2 border-primary" : "text-content-secondary"
          }`}
        >
          <ListMusic className="w-4 h-4" />
          <span>Queue ({room.queue_tracks?.length ?? 0})</span>
        </button>

        <button
          onClick={() => {
            haptic("light");
            setActiveTab("members");
          }}
          className={`flex items-center space-x-1.5 py-1 px-3 text-xs font-semibold transition-colors ${
            activeTab === "members" ? "text-primary border-b-2 border-primary" : "text-content-secondary"
          }`}
        >
          <Users className="w-4 h-4" />
          <span>Members ({room.members?.length ? room.members.length + 1 : 1})</span>
        </button>
      </div>

      {/* ─── TAB CONTENT ──────────────────────────────────────────────────── */}
      {activeTab === "chat" && (
        <div className="flex flex-col h-[320px] rounded-2xl bg-surface/60 border border-border p-3">
          <div className="flex-1 overflow-y-auto space-y-2.5 pr-1">
            {messages.length === 0 ? (
              <div className="h-full flex items-center justify-center text-xs text-content-muted">
                Say hello to everyone in the room!
              </div>
            ) : (
              messages.map((m) => (
                <div key={m.id} className="flex flex-col text-xs space-y-0.5">
                  <span className="font-bold text-accent">{m.display_name}</span>
                  <span className="text-content-primary bg-surface-hover/60 px-2.5 py-1.5 rounded-xl w-fit max-w-[85%] break-words">
                    {m.content}
                  </span>
                </div>
              ))
            )}
          </div>

          <form onSubmit={handleSendMessage} className="flex items-center space-x-2 mt-2 pt-2 border-t border-border/60">
            <input
              type="text"
              value={newMessage}
              onChange={(e) => setNewMessage(e.target.value)}
              placeholder="Send message..."
              className="flex-1 px-3 py-2 rounded-xl bg-surface-hover border border-border text-xs text-content-primary focus:outline-none focus:border-primary"
            />
            <button
              type="submit"
              disabled={!newMessage.trim()}
              className="w-8 h-8 flex items-center justify-center rounded-xl bg-primary text-white disabled:opacity-40"
            >
              <Send className="w-4 h-4" />
            </button>
          </form>
        </div>
      )}

      {activeTab === "queue" && (
        <div className="flex flex-col space-y-2">
          {(!room.queue_tracks || room.queue_tracks.length === 0) ? (
            <div className="py-12 text-center text-xs text-content-muted">
              Queue is empty.
            </div>
          ) : (
            room.queue_tracks.map((t, idx) => (
              <div
                key={t.id}
                className="flex items-center justify-between p-2.5 rounded-xl bg-surface/60 border border-border/40"
              >
                <div className="flex items-center space-x-3 overflow-hidden">
                  <span className="text-xs font-mono text-content-muted w-4">{idx + 1}</span>
                  <div className="flex flex-col min-w-0">
                    <span className="text-sm font-semibold text-content-primary truncate">{t.title}</span>
                    <span className="text-xs text-content-secondary truncate">{t.artist_name}</span>
                  </div>
                </div>

                {canControl && (
                  <button
                    onClick={() => {
                      haptic("light");
                      playTrack(t);
                      api.rooms.updatePlayback(roomId, {
                        current_track_id: t.id,
                        playback_position_ms: 0,
                        is_playing: true,
                      }).catch(() => {});
                    }}
                    className="w-8 h-8 flex items-center justify-center rounded-full bg-white/5 hover:bg-primary text-content-primary hover:text-white transition-colors"
                  >
                    <Play className="w-3.5 h-3.5 fill-current ml-0.5" />
                  </button>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {activeTab === "members" && (
        <div className="flex flex-col space-y-2">
          {/* Host Item */}
          <div className="flex items-center justify-between p-3 rounded-xl bg-surface/80 border border-border">
            <div className="flex items-center space-x-3">
              <div className="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center text-primary font-bold text-xs">
                <Crown className="w-4 h-4" />
              </div>
              <div className="flex flex-col">
                <span className="text-sm font-bold text-content-primary">{room.host_name}</span>
                <span className="text-[10px] text-primary uppercase font-bold">Room Host</span>
              </div>
            </div>
          </div>

          {/* Members Items */}
          {room.members?.map((m) => (
            <div
              key={m.user_id}
              className="flex items-center justify-between p-3 rounded-xl bg-surface/60 border border-border/40"
            >
              <div className="flex items-center space-x-3">
                <div className="w-8 h-8 rounded-full bg-surface-light border border-border flex items-center justify-center text-content-secondary text-xs font-bold">
                  {m.display_name.charAt(0).toUpperCase()}
                </div>
                <div className="flex flex-col">
                  <span className="text-sm font-semibold text-content-primary">{m.display_name}</span>
                  <span className="text-[10px] text-content-muted">
                    {m.can_control_playback ? "Can DJ" : "Listener"}
                  </span>
                </div>
              </div>

              {isHost && (
                <div className="flex items-center space-x-1">
                  <button
                    onClick={async () => {
                      haptic("light");
                      await api.rooms.updateMemberPermissions(roomId, m.user_id, {
                        can_control_playback: !m.can_control_playback,
                        can_manage_queue: !m.can_manage_queue,
                      });
                      fetchRoomData();
                    }}
                    className={`px-2 py-1 rounded-lg text-[10px] font-bold uppercase transition-colors ${
                      m.can_control_playback
                        ? "bg-accent/20 text-accent"
                        : "bg-white/5 text-content-muted hover:text-content-primary"
                    }`}
                  >
                    {m.can_control_playback ? "Revoke DJ" : "Make DJ"}
                  </button>

                  <button
                    onClick={async () => {
                      haptic("medium");
                      await api.rooms.kickMember(roomId, m.user_id);
                      fetchRoomData();
                    }}
                    className="p-1 rounded-lg text-red-400 hover:bg-red-500/10"
                    title="Kick member"
                  >
                    <UserX className="w-4 h-4" />
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
