"use client";

import React, { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Play, Shuffle, Trash2, ArrowLeft, Plus, Lock, Globe } from "lucide-react";
import { Playlist, Track } from "@/lib/types";
import { api } from "@/lib/api";
import { TrackCard } from "@/components/ui/track-card";
import { usePlayer } from "@/contexts/player-context";
import { useTelegram } from "@/contexts/telegram-context";

export default function PlaylistDetailPage() {
  const params = useParams();
  const playlistId = params?.id as string;
  const router = useRouter();

  const [playlist, setPlaylist] = useState<Playlist | null>(null);
  const [allTracks, setAllTracks] = useState<Track[]>([]);
  const [showAddModal, setShowAddModal] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  const { playTrack } = usePlayer();
  const { haptic } = useTelegram();

  const loadData = async () => {
    try {
      setIsLoading(true);
      const pl = await api.playlists.get(playlistId);
      setPlaylist(pl);

      // Load all available library tracks for adding
      const res = await api.tracks.list("", 100);
      setAllTracks(res.items);
    } catch {
      // not found
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [playlistId]);

  // Telegram BackButton support
  useEffect(() => {
    const tg = typeof window !== "undefined" ? (window as any).Telegram?.WebApp : null;
    const backBtn = tg?.BackButton;

    const handleBack = () => {
      haptic("light");
      if (showAddModal) {
        setShowAddModal(false);
      } else {
        router.push("/playlist");
      }
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
  }, [showAddModal, router, haptic]);

  const handlePlayAll = () => {
    if (playlist?.tracks && playlist.tracks.length > 0) {
      haptic("light");
      playTrack(playlist.tracks[0], playlist.tracks);
    }
  };

  const handleShufflePlay = () => {
    if (playlist?.tracks && playlist.tracks.length > 0) {
      haptic("light");
      const shuffled = [...playlist.tracks].sort(() => Math.random() - 0.5);
      playTrack(shuffled[0], shuffled);
    }
  };

  const handleDelete = async () => {
    if (!confirm("Are you sure you want to delete this playlist?")) return;
    haptic("medium");
    try {
      await api.playlists.delete(playlistId);
      router.push("/playlist");
    } catch {}
  };

  const handleAddTrack = async (trackId: string) => {
    haptic("light");
    try {
      await api.playlists.addTrack(playlistId, trackId);
      loadData();
    } catch (err: any) {
      alert(err.message || "Failed to add track");
    }
  };

  if (isLoading || !playlist) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] space-y-3">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        <span className="text-xs text-content-secondary">Loading playlist...</span>
      </div>
    );
  }

  const tracks = playlist.tracks || [];
  const artwork = playlist.preview_artwork || "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=400&q=80";

  return (
    <div className="flex flex-col space-y-5 pb-8">
      {/* Back button */}
      <div className="flex items-center justify-between pt-2">
        <button
          onClick={() => router.push("/playlist")}
          className="flex items-center space-x-1 text-content-secondary hover:text-content-primary text-xs font-semibold"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Playlists</span>
        </button>

        {playlist.is_owner && (
          <button
            onClick={handleDelete}
            className="p-1.5 rounded-lg text-content-muted hover:text-red-400 hover:bg-red-500/10 transition-colors"
            title="Delete playlist"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Playlist Hero */}
      <div className="flex items-center space-x-4 p-4 rounded-3xl bg-surface/80 border border-border">
        <div className="relative w-24 h-24 rounded-2xl overflow-hidden bg-surface-light border border-border/60 flex-shrink-0 shadow-lg">
          <img src={artwork} alt="" className="w-full h-full object-cover" />
        </div>

        <div className="flex flex-col min-w-0">
          <div className="flex items-center space-x-2">
            <span className="text-[10px] uppercase font-bold tracking-widest text-primary">
              Playlist
            </span>
            {playlist.is_public ? (
              <span className="flex items-center space-x-0.5 text-[10px] text-accent">
                <Globe className="w-3 h-3" />
                <span>Public</span>
              </span>
            ) : (
              <span className="flex items-center space-x-0.5 text-[10px] text-content-muted">
                <Lock className="w-3 h-3" />
                <span>Private</span>
              </span>
            )}
          </div>
          <h1 className="text-xl font-extrabold text-content-primary truncate mt-0.5">
            {playlist.name}
          </h1>
          {playlist.description && (
            <p className="text-xs text-content-secondary line-clamp-2 mt-1">
              {playlist.description}
            </p>
          )}
          <span className="text-xs text-content-muted font-mono mt-1">
            {tracks.length} {tracks.length === 1 ? "track" : "tracks"}
          </span>
        </div>
      </div>

      {/* Action Buttons */}
      <div className="flex items-center space-x-3">
        <button
          onClick={handlePlayAll}
          disabled={tracks.length === 0}
          className="flex-1 flex items-center justify-center space-x-2 py-3 rounded-2xl bg-primary hover:bg-primary-hover text-white font-bold text-xs shadow-lg shadow-primary/25 disabled:opacity-40 transition-all active:scale-[0.98]"
        >
          <Play className="w-4 h-4 fill-current ml-0.5" />
          <span>Play All</span>
        </button>

        <button
          onClick={handleShufflePlay}
          disabled={tracks.length === 0}
          className="flex-1 flex items-center justify-center space-x-2 py-3 rounded-2xl bg-surface-hover hover:bg-surface-light border border-border text-content-primary font-bold text-xs disabled:opacity-40 transition-all active:scale-[0.98]"
        >
          <Shuffle className="w-4 h-4" />
          <span>Shuffle</span>
        </button>

        {playlist.is_owner && (
          <button
            onClick={() => setShowAddModal(true)}
            className="w-12 h-12 flex items-center justify-center rounded-2xl bg-surface-hover border border-border text-content-secondary hover:text-content-primary"
            title="Add track"
          >
            <Plus className="w-5 h-5" />
          </button>
        )}
      </div>

      {/* Tracks List */}
      <div className="flex flex-col space-y-1">
        {tracks.length === 0 ? (
          <div className="py-12 text-center text-xs text-content-muted">
            This playlist is empty. Tap the + button to add songs.
          </div>
        ) : (
          tracks.map((track, idx) => (
            <TrackCard
              key={track.id}
              track={track}
              index={idx}
              onSelect={() => playTrack(track, tracks)}
            />
          ))
        )}
      </div>

      {/* ─── ADD TRACK MODAL ──────────────────────────────────────────────── */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-end sm:items-center justify-center p-4">
          <div className="w-full max-w-sm max-h-[80vh] flex flex-col bg-surface border border-border rounded-3xl p-5 shadow-2xl">
            <div className="flex items-center justify-between pb-3 border-b border-border/60">
              <h2 className="text-base font-bold text-content-primary">Add Track to Playlist</h2>
              <button
                onClick={() => setShowAddModal(false)}
                className="text-xs text-content-secondary hover:text-white"
              >
                Done
              </button>
            </div>

            <div className="flex-1 overflow-y-auto space-y-1 mt-3">
              {allTracks.map((t) => {
                const inPlaylist = tracks.some((pt) => pt.id === t.id);
                return (
                  <div
                    key={t.id}
                    className="flex items-center justify-between p-2 rounded-xl hover:bg-white/5"
                  >
                    <div className="flex flex-col min-w-0 pr-2">
                      <span className="text-sm font-semibold truncate text-content-primary">
                        {t.title}
                      </span>
                      <span className="text-xs text-content-secondary truncate">
                        {t.artist_name}
                      </span>
                    </div>

                    <button
                      onClick={() => handleAddTrack(t.id)}
                      disabled={inPlaylist}
                      className={`px-3 py-1 rounded-lg text-xs font-semibold transition-colors ${
                        inPlaylist
                          ? "bg-white/5 text-content-muted"
                          : "bg-primary text-white hover:bg-primary-hover"
                      }`}
                    >
                      {inPlaylist ? "Added" : "Add"}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
