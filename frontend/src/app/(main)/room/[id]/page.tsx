"use client";

import React, { useEffect, useState } from "react";
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
  Check,
  LogOut,
  Send,
  UserX,
  ListMusic,
  ThumbsUp,
  ThumbsDown,
  Volume2,
  Radio,
} from "lucide-react";
import { ChatMessage, Track, QueueItem } from "@/lib/types";
import { api } from "@/lib/api";
import { TrackArtwork } from "@/components/ui/track-artwork";
import { useAuth } from "@/contexts/auth-context";
import { usePlayer } from "@/contexts/player-context";
import { useTelegram } from "@/contexts/telegram-context";
import { useRoomSync } from "@/hooks/use-room-sync";

export default function RoomDetailPage() {
  const params = useParams();
  const roomId = params?.id as string;
  const router = useRouter();

  const { user } = useAuth();
  const { isPlaying, currentTime, playTrack } = usePlayer();
  const { haptic } = useTelegram();

  const {
    room,
    normalizedQueue,
    currentDriftMs,
    syncStatus,
    needsAudioUnlock,
    unlockAudio,
    sendCommand,
    refreshState,
  } = useRoomSync(roomId);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [newMessage, setNewMessage] = useState("");
  const [activeTab, setActiveTab] = useState<"chat" | "queue" | "members">("chat");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleShare = async () => {
    haptic("light");
    const botUsername = process.env.NEXT_PUBLIC_BOT_USERNAME || "AfterhoursAudioBot";
    const inviteUrl = `https://t.me/${botUsername}?startapp=room_${roomId}`;

    if (typeof navigator !== "undefined" && navigator.share) {
      try {
        await navigator.share({
          title: `Join ${room?.name || "Listening Room"} on Afterhours`,
          text: `🎧 Join my synchronized listening room "${room?.name || "Afterhours Room"}"!`,
          url: inviteUrl,
        });
        return;
      } catch {
        // Fallback to clipboard
      }
    }

    try {
      if (typeof navigator !== "undefined" && navigator.clipboard) {
        await navigator.clipboard.writeText(inviteUrl);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }
    } catch {
      // ignore
    }
  };

  // Telegram BackButton support
  useEffect(() => {
    const tg = typeof window !== "undefined" ? (window as any).Telegram?.WebApp : null;
    const backBtn = tg?.BackButton;

    const handleBack = () => {
      haptic("light");
      router.push("/room");
    };

    if (backBtn) {
      backBtn.show();
      backBtn.onClick(handleBack);
    }

    return () => {
      if (backBtn) {
        backBtn.offClick(handleBack);
        backBtn.hide();
      }
    };
  }, [router, haptic]);

  // Sync chat messages
  useEffect(() => {
    let isSubscribed = true;
    const fetchMessages = async () => {
      try {
        const msgs = await api.rooms.getMessages(roomId);
        if (isSubscribed) setMessages(msgs);
      } catch {}
    };

    fetchMessages();
    const interval = setInterval(fetchMessages, 3000);
    return () => {
      isSubscribed = false;
      clearInterval(interval);
    };
  }, [roomId]);

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newMessage.trim() || isSubmitting) return;

    haptic("light");
    const content = newMessage.trim();
    setNewMessage("");
    setIsSubmitting(true);

    try {
      const sent = await api.rooms.sendMessage(roomId, content);
      setMessages((prev) => [...prev, sent]);
    } catch {
      // ignore
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleLeaveRoom = async () => {
    haptic("medium");
    try {
      await api.rooms.leave(roomId);
    } catch {}
    router.push("/room");
  };

  const handlePlaybackToggle = async () => {
    haptic("light");
    try {
      if (room?.is_playing) {
        await sendCommand("PAUSE", { position_ms: Math.round(currentTime * 1000) });
      } else {
        await sendCommand("PLAY", { position_ms: Math.round(currentTime * 1000) });
      }
    } catch (err: any) {
      console.warn("Playback command error:", err);
    }
  };

  const handleNextTrack = async () => {
    haptic("light");
    try {
      await sendCommand("NEXT_TRACK");
    } catch (err: any) {
      console.warn("Next track error:", err);
    }
  };

  const handleVote = async (queueItemId: string, vote: number) => {
    haptic("light");
    try {
      await sendCommand("QUEUE_VOTE", { queue_item_id: queueItemId, vote });
    } catch (err: any) {
      console.warn("Vote error:", err);
    }
  };

  if (!room) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] space-y-3">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        <span className="text-xs text-content-secondary">Calibrating room sync...</span>
      </div>
    );
  }

  const isHost = user?.id === room.host_id;
  const canControl = room.user_permissions?.can_control_playback || isHost;
  const canManageQueue = room.user_permissions?.can_manage_queue || isHost;

  return (
    <div className="flex flex-col space-y-4">
      {/* ─── iOS / TELEGRAM WEBVIEW GESTURE GATE ──────────────────────────── */}
      {needsAudioUnlock && (
        <div className="flex items-center justify-between p-3.5 rounded-2xl bg-amber-500/10 border border-amber-500/30 text-amber-200 shadow-md">
          <div className="flex items-center space-x-2.5 min-w-0 pr-2">
            <Volume2 className="w-5 h-5 shrink-0 text-amber-400" />
            <div className="flex flex-col">
              <span className="text-xs font-bold text-amber-300">Tap to Sync Audio</span>
              <span className="text-[10px] text-amber-200/70">iOS Safari and Telegram WebView require gesture priming</span>
            </div>
          </div>
          <button
            onClick={() => {
              haptic("medium");
              unlockAudio();
            }}
            className="px-3.5 py-1.5 text-xs font-bold bg-amber-400 text-black rounded-xl hover:bg-amber-300 shrink-0 shadow-sm"
          >
            Sync Now
          </button>
        </div>
      )}

      {/* ─── ROOM HEADER ─────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between p-4 rounded-2xl bg-surface/90 border border-border">
        <div className="flex flex-col min-w-0 pr-2">
          <div className="flex items-center space-x-2">
            <h1 className="text-lg font-bold text-content-primary truncate">{room.name}</h1>
            <span className="px-2 py-0.5 rounded-full bg-primary/20 text-primary text-[10px] font-bold shrink-0">
              {room.listener_count} Listening
            </span>
            {/* Sync Status Badge */}
            <span
              className={`px-2 py-0.5 rounded-full text-[9px] font-mono font-bold shrink-0 ${
                syncStatus === "synced"
                  ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                  : syncStatus === "adjusting"
                  ? "bg-amber-500/20 text-amber-400 border border-amber-500/30 animate-pulse"
                  : "bg-blue-500/20 text-blue-400 border border-blue-500/30"
              }`}
              title={`Sync: ${syncStatus} (drift: ${currentDriftMs}ms)`}
            >
              {syncStatus.toUpperCase()} {Math.abs(currentDriftMs) > 20 && `(${Math.abs(currentDriftMs)}ms)`}
            </span>
          </div>
          <span className="text-xs text-content-secondary mt-0.5">
            Host: <span className="text-content-primary font-medium">{room.host_name}</span>
          </span>
        </div>

        <div className="flex items-center space-x-2">
          <button
            onClick={handleShare}
            className={`w-9 h-9 flex items-center justify-center rounded-full transition-all ${
              copied
                ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                : "bg-white/5 text-content-secondary hover:text-content-primary"
            }`}
            title={copied ? "Link copied!" : "Share room invite"}
          >
            {copied ? <Check className="w-4 h-4 text-emerald-400" /> : <Share2 className="w-4 h-4" />}
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
          <div className="relative w-12 h-12 flex-shrink-0 rounded-xl overflow-hidden border border-border/40">
            <TrackArtwork
              src={room.current_track?.artwork_thumb_url || room.current_track?.artwork_url}
              alt={room.current_track?.title || "Track"}
            />
          </div>
          <div className="flex flex-col min-w-0">
            <div className="flex items-center space-x-1.5">
              <Radio className="w-3 h-3 text-primary animate-pulse" />
              <span className="text-[10px] uppercase font-bold tracking-wider text-primary">Now Playing</span>
            </div>
            <span className="text-sm font-bold text-content-primary truncate">
              {room.current_track?.title || "No track active"}
            </span>
            <span className="text-xs text-content-secondary truncate">
              {room.current_track?.artist_name || "Host can select a track from queue"}
            </span>
          </div>
        </div>

        {canControl && (
          <div className="flex items-center space-x-2">
            <button
              onClick={handlePlaybackToggle}
              className="w-11 h-11 flex items-center justify-center rounded-full bg-primary hover:bg-primary-hover text-white shadow-lg shadow-primary/30 transition-all active:scale-95"
            >
              {room.is_playing ? <Pause className="w-5 h-5 fill-current" /> : <Play className="w-5 h-5 fill-current ml-0.5" />}
            </button>
            <button
              onClick={handleNextTrack}
              className="w-9 h-9 flex items-center justify-center rounded-full bg-white/5 hover:bg-white/10 text-content-primary transition-all active:scale-95"
              title="Next Track"
            >
              <SkipForward className="w-4 h-4" />
            </button>
          </div>
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
          <span>Queue ({normalizedQueue.length > 0 ? normalizedQueue.length : room.queue_tracks?.length ?? 0})</span>
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
          {normalizedQueue.length > 0 ? (
            normalizedQueue.map((item, idx) => {
              const track = item.tracks;
              const title = track?.title || `Track ${idx + 1}`;
              const artist = track?.artist_name || "Unknown Artist";
              return (
                <div
                  key={item.id}
                  className="flex items-center justify-between p-2.5 rounded-xl bg-surface/60 border border-border/40"
                >
                  <div className="flex items-center space-x-3 overflow-hidden flex-1 min-w-0 pr-2">
                    <span className="text-xs font-mono text-content-muted w-4 shrink-0">{idx + 1}</span>
                    <div className="flex flex-col min-w-0">
                      <span className="text-sm font-semibold text-content-primary truncate">{title}</span>
                      <span className="text-xs text-content-secondary truncate">{artist}</span>
                    </div>
                  </div>

                  <div className="flex items-center space-x-1.5 shrink-0">
                    {/* Voting Controls */}
                    <div className="flex items-center space-x-1 bg-surface-light px-2 py-1 rounded-lg border border-border/40">
                      <button
                        onClick={() => handleVote(item.id, 1)}
                        className="text-content-muted hover:text-emerald-400 transition-colors p-0.5"
                        title="Upvote"
                      >
                        <ThumbsUp className="w-3.5 h-3.5" />
                      </button>
                      <span className="text-xs font-bold font-mono text-content-primary min-w-[16px] text-center">
                        {item.vote_count ?? 0}
                      </span>
                      <button
                        onClick={() => handleVote(item.id, -1)}
                        className="text-content-muted hover:text-red-400 transition-colors p-0.5"
                        title="Downvote"
                      >
                        <ThumbsDown className="w-3.5 h-3.5" />
                      </button>
                    </div>

                    {canControl && track && (
                      <button
                        onClick={() => {
                          haptic("light");
                          sendCommand("PLAY", { track_id: track.id, position_ms: 0 });
                        }}
                        className="w-7 h-7 flex items-center justify-center rounded-full bg-white/5 hover:bg-primary text-content-primary hover:text-white transition-colors"
                        title="Play now"
                      >
                        <Play className="w-3 h-3 fill-current ml-0.5" />
                      </button>
                    )}
                  </div>
                </div>
              );
            })
          ) : room.queue_tracks && room.queue_tracks.length > 0 ? (
            // Backward-compatible fallback for legacy queue
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
                      sendCommand("PLAY", { track_id: t.id, position_ms: 0 });
                    }}
                    className="w-8 h-8 flex items-center justify-center rounded-full bg-white/5 hover:bg-primary text-content-primary hover:text-white transition-colors"
                  >
                    <Play className="w-3.5 h-3.5 fill-current ml-0.5" />
                  </button>
                )}
              </div>
            ))
          ) : (
            <div className="py-12 text-center text-xs text-content-muted">Queue is empty.</div>
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
                  <span className="text-[10px] text-content-muted capitalize">
                    {m.role || (m.can_control_playback ? "DJ" : "Listener")}
                  </span>
                </div>
              </div>

              {isHost && (
                <div className="flex items-center space-x-1">
                  <button
                    onClick={async () => {
                      haptic("light");
                      const nextRole = m.role === "dj" ? "listener" : "dj";
                      await api.rooms.updateMemberPermissions(roomId, m.user_id, {
                        can_control_playback: nextRole === "dj",
                        can_manage_queue: nextRole === "dj",
                      });
                      refreshState();
                    }}
                    className={`px-2 py-1 rounded-lg text-[10px] font-bold uppercase transition-colors ${
                      m.role === "dj" || m.can_control_playback
                        ? "bg-accent/20 text-accent"
                        : "bg-white/5 text-content-muted hover:text-content-primary"
                    }`}
                  >
                    {m.role === "dj" || m.can_control_playback ? "Revoke DJ" : "Make DJ"}
                  </button>

                  <button
                    onClick={async () => {
                      haptic("medium");
                      await api.rooms.kickMember(roomId, m.user_id);
                      refreshState();
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
