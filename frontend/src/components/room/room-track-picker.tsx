"use client";

import React, { useState, useEffect, useMemo } from "react";
import { Search, X, Play, ListPlus, Loader2, Check, Music } from "lucide-react";
import { Track } from "@/lib/types";
import { api } from "@/lib/api";
import { TrackArtwork } from "@/components/ui/track-artwork";
import { useTelegram } from "@/contexts/telegram-context";

interface RoomTrackPickerProps {
  isOpen: boolean;
  onClose: () => void;
  onPlayNow: (track: Track) => Promise<void>;
  onAddToQueue: (track: Track) => Promise<void>;
  currentTrackId?: string | null;
}

function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds < 10 ? "0" : ""}${seconds}`;
}

export function RoomTrackPicker({
  isOpen,
  onClose,
  onPlayNow,
  onAddToQueue,
  currentTrackId,
}: RoomTrackPickerProps) {
  const { haptic } = useTelegram();
  const [tracks, setTracks] = useState<Track[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [activeActionId, setActiveActionId] = useState<string | null>(null);
  const [addedTrackIds, setAddedTrackIds] = useState<Set<string>>(new Set());

  // Load tracks when opened
  useEffect(() => {
    if (!isOpen) return;

    let isSubscribed = true;
    setLoading(true);

    api.tracks
      .list(undefined, 100, 0)
      .then((res) => {
        if (isSubscribed) {
          setTracks(res.items || []);
        }
      })
      .catch((err) => {
        console.warn("Failed to load tracks for picker:", err);
      })
      .finally(() => {
        if (isSubscribed) setLoading(false);
      });

    return () => {
      isSubscribed = false;
    };
  }, [isOpen]);

  const filteredTracks = useMemo(() => {
    if (!searchQuery.trim()) return tracks;
    const q = searchQuery.toLowerCase().trim();
    return tracks.filter(
      (t) =>
        t.title.toLowerCase().includes(q) ||
        t.artist_name.toLowerCase().includes(q) ||
        t.album_title.toLowerCase().includes(q)
    );
  }, [tracks, searchQuery]);

  if (!isOpen) return null;

  const handlePlayNow = async (track: Track) => {
    haptic("medium");
    setActiveActionId(track.id + "_play");
    try {
      await onPlayNow(track);
      onClose();
    } catch (err) {
      console.error("Play now failed:", err);
    } finally {
      setActiveActionId(null);
    }
  };

  const handleAddToQueue = async (track: Track) => {
    haptic("light");
    setActiveActionId(track.id + "_queue");
    try {
      await onAddToQueue(track);
      setAddedTrackIds((prev) => new Set(prev).add(track.id));
      setTimeout(() => {
        setAddedTrackIds((prev) => {
          const next = new Set(prev);
          next.delete(track.id);
          return next;
        });
      }, 2500);
    } catch (err) {
      console.error("Add to queue failed:", err);
    } finally {
      setActiveActionId(null);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div
        className="w-full max-w-lg max-h-[85vh] sm:max-h-[80vh] flex flex-col rounded-t-3xl sm:rounded-2xl bg-surface border border-border shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-border/80 bg-surface/90">
          <div className="flex items-center space-x-2.5">
            <div className="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center text-primary">
              <Music className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-base font-bold text-content-primary">Select Music for Room</h2>
              <p className="text-[11px] text-content-secondary">Play directly or append to queue</p>
            </div>
          </div>
          <button
            onClick={() => {
              haptic("light");
              onClose();
            }}
            className="w-8 h-8 flex items-center justify-center rounded-full bg-surface-hover text-content-muted hover:text-content-primary transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Search Input */}
        <div className="p-3 border-b border-border/60 bg-surface/60">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-content-muted" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search title, artist, or album..."
              className="w-full pl-9 pr-8 py-2 rounded-xl bg-surface-hover border border-border text-xs text-content-primary placeholder:text-content-muted focus:outline-none focus:border-primary transition-colors"
              autoFocus
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-content-muted hover:text-content-primary"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>

        {/* Track List */}
        <div className="flex-1 overflow-y-auto divide-y divide-border/30 p-2 space-y-1 min-h-[250px]">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-16 space-y-2">
              <Loader2 className="w-6 h-6 animate-spin text-primary" />
              <span className="text-xs text-content-secondary">Loading music library...</span>
            </div>
          ) : filteredTracks.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center text-xs text-content-muted">
              <Music className="w-8 h-8 mb-2 opacity-30" />
              <span>No tracks found.</span>
            </div>
          ) : (
            filteredTracks.map((track) => {
              const isCurrent = track.id === currentTrackId;
              const isPlayingAction = activeActionId === track.id + "_play";
              const isQueueAction = activeActionId === track.id + "_queue";
              const wasAdded = addedTrackIds.has(track.id);

              return (
                <div
                  key={track.id}
                  className={`flex items-center justify-between p-2 rounded-xl transition-all ${
                    isCurrent ? "bg-primary/10 border border-primary/30" : "hover:bg-surface-hover"
                  }`}
                >
                  {/* Artwork + Title/Artist */}
                  <div className="flex items-center space-x-3 min-w-0 pr-2 flex-1">
                    <div className="relative w-11 h-11 rounded-lg overflow-hidden shrink-0 border border-border/40">
                      <TrackArtwork
                        src={track.artwork_thumb_url || track.artwork_url}
                        alt={track.title}
                      />
                    </div>
                    <div className="flex flex-col min-w-0">
                      <div className="flex items-center space-x-1.5">
                        <span className="text-xs font-bold text-content-primary truncate">
                          {track.title}
                        </span>
                        {isCurrent && (
                          <span className="px-1.5 py-0.2 rounded bg-primary text-white text-[9px] font-bold shrink-0">
                            PLAYING
                          </span>
                        )}
                      </div>
                      <span className="text-[11px] text-content-secondary truncate">
                        {track.artist_name} • {formatDuration(track.duration_ms)}
                      </span>
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center space-x-1.5 shrink-0">
                    {/* Add to Queue */}
                    <button
                      onClick={() => handleAddToQueue(track)}
                      disabled={isQueueAction}
                      className={`px-2.5 py-1.5 rounded-lg text-[11px] font-semibold flex items-center space-x-1 transition-all ${
                        wasAdded
                          ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                          : "bg-surface-light hover:bg-white/10 text-content-secondary hover:text-content-primary border border-border/60"
                      }`}
                      title="Add to queue"
                    >
                      {wasAdded ? (
                        <>
                          <Check className="w-3.5 h-3.5 text-emerald-400" />
                          <span>Added</span>
                        </>
                      ) : isQueueAction ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />
                      ) : (
                        <>
                          <ListPlus className="w-3.5 h-3.5" />
                          <span className="hidden sm:inline">Queue</span>
                        </>
                      )}
                    </button>

                    {/* Play Now */}
                    <button
                      onClick={() => handlePlayNow(track)}
                      disabled={isPlayingAction || isCurrent}
                      className={`px-3 py-1.5 rounded-lg text-[11px] font-bold flex items-center space-x-1 transition-all ${
                        isCurrent
                          ? "opacity-40 bg-surface-light text-content-muted cursor-not-allowed"
                          : "bg-primary hover:bg-primary-hover text-white shadow-sm"
                      }`}
                      title="Play now"
                    >
                      {isPlayingAction ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin text-white" />
                      ) : (
                        <>
                          <Play className="w-3.5 h-3.5 fill-current ml-0.5" />
                          <span>Play</span>
                        </>
                      )}
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
