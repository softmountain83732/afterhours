"use client";

import React, { useState, useEffect } from "react";
import {
  Heart,
  ListPlus,
  ListEnd,
  PlaySquare,
  Disc,
  User,
  Share2,
  Check,
  X,
  ArrowLeft,
  Loader2,
  Music,
} from "lucide-react";
import { Track, Playlist } from "@/lib/types";
import { api } from "@/lib/api";
import { usePlayer } from "@/contexts/player-context";
import { useTelegram } from "@/contexts/telegram-context";

interface TrackContextMenuProps {
  track: Track | null;
  isOpen: boolean;
  onClose: () => void;
  onFilterArtist?: (artistName: string) => void;
  onFilterAlbum?: (albumTitle: string) => void;
}

function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds < 10 ? "0" : ""}${seconds}`;
}

export const TrackContextMenu: React.FC<TrackContextMenuProps> = ({
  track,
  isOpen,
  onClose,
  onFilterArtist,
  onFilterAlbum,
}) => {
  const { toggleFavorite, addToQueue, playNext } = usePlayer();
  const { haptic } = useTelegram();

  const [view, setView] = useState<"menu" | "playlists">("menu");
  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [isLoadingPlaylists, setIsLoadingPlaylists] = useState(false);
  const [playlistActionStatus, setPlaylistActionStatus] = useState<{
    id: string;
    status: "loading" | "done";
  } | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Reset view when opening/closing
  useEffect(() => {
    if (!isOpen) {
      setView("menu");
      setToastMessage(null);
      setPlaylistActionStatus(null);
    }
  }, [isOpen]);

  const showToast = (msg: string, autoClose = true) => {
    setToastMessage(msg);
    if (autoClose) {
      setTimeout(() => {
        setToastMessage(null);
        onClose();
      }, 1200);
    } else {
      setTimeout(() => setToastMessage(null), 2500);
    }
  };

  if (!isOpen || !track) return null;

  const artwork =
    track.artwork_thumb_url ||
    track.artwork_url ||
    "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=300&q=80";

  const handleToggleFav = async () => {
    haptic("medium");
    try {
      const willBeFav = !track.is_favorite;
      await toggleFavorite(track);
      showToast(willBeFav ? "Added to Favorites" : "Removed from Favorites");
    } catch {
      showToast("Could not update favorites", false);
    }
  };

  const handleAddToQueue = () => {
    haptic("light");
    addToQueue(track);
    showToast("Added to queue");
  };

  const handlePlayNext = () => {
    haptic("light");
    playNext(track);
    showToast("Playing next");
  };

  const handleOpenPlaylists = async () => {
    haptic("light");
    setView("playlists");
    setIsLoadingPlaylists(true);
    try {
      const userPlaylists = await api.playlists.list();
      setPlaylists(userPlaylists);
    } catch {
      setPlaylists([]);
    } finally {
      setIsLoadingPlaylists(false);
    }
  };

  const handleAddTrackToPlaylist = async (playlist: Playlist) => {
    haptic("medium");
    setPlaylistActionStatus({ id: playlist.id, status: "loading" });
    try {
      await api.playlists.addTrack(playlist.id, track.id);
      setPlaylistActionStatus({ id: playlist.id, status: "done" });
      showToast(`Added to ${playlist.name}`);
    } catch {
      setPlaylistActionStatus(null);
      showToast("Failed to add to playlist", false);
    }
  };

  const handleFilterByArtist = () => {
    haptic("light");
    if (onFilterArtist && track.artist_name) {
      onFilterArtist(track.artist_name);
    }
    onClose();
  };

  const handleFilterByAlbum = () => {
    haptic("light");
    if (onFilterAlbum && track.album_title) {
      onFilterAlbum(track.album_title);
    }
    onClose();
  };

  const handleShare = async () => {
    haptic("light");
    const shareUrl = `https://t.me/AfterhoursAudioBot?startapp=track_${track.id}`;
    const shareText = `Listen to ${track.title} by ${track.artist_name} on Afterhours`;

    if (typeof navigator !== "undefined" && navigator.share) {
      try {
        await navigator.share({
          title: track.title,
          text: shareText,
          url: shareUrl,
        });
        showToast("Shared successfully");
        return;
      } catch {
        // Fallback to clipboard
      }
    }

    if (typeof navigator !== "undefined" && navigator.clipboard) {
      try {
        await navigator.clipboard.writeText(shareUrl);
        showToast("Link copied to clipboard");
        return;
      } catch {
        // Ignore clipboard failure
      }
    }

    showToast("Share link ready");
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center sm:justify-center bg-black/70 backdrop-blur-sm transition-opacity animate-fade-in"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full sm:max-w-md bg-surface border-t sm:border border-border rounded-t-3xl sm:rounded-2xl max-h-[85vh] overflow-hidden flex flex-col shadow-2xl animate-slide-up"
      >
        {/* Grab bar for mobile bottom sheet */}
        <div className="w-full flex justify-center pt-3 pb-1 sm:hidden">
          <div className="w-10 h-1 bg-border rounded-full" />
        </div>

        {/* Track Header Card */}
        <div className="p-4 flex items-center space-x-3 border-b border-border/50">
          <div className="relative w-14 h-14 rounded-xl overflow-hidden bg-surface-light border border-border/60 flex-shrink-0">
            <img
              src={artwork}
              alt=""
              className="w-full h-full object-cover"
              onError={(e) => {
                (e.target as HTMLImageElement).src =
                  "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=300&q=80";
              }}
            />
          </div>

          <div className="flex-1 min-w-0">
            <h3 className="text-base font-bold text-content-primary truncate">
              {track.title}
            </h3>
            <p className="text-xs text-content-secondary truncate mt-0.5">
              {track.artist_name}
            </p>
            <div className="flex items-center space-x-2 mt-1">
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-light text-content-muted uppercase">
                {track.codec || "audio"}
              </span>
              <span className="text-[10px] font-mono text-content-muted">
                {formatDuration(track.duration_ms)}
              </span>
            </div>
          </div>

          <button
            onClick={() => {
              haptic("light");
              onClose();
            }}
            aria-label="Close"
            className="w-8 h-8 rounded-full flex items-center justify-center text-content-muted hover:text-content-primary hover:bg-surface-light transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Toast Alert Banner */}
        {toastMessage && (
          <div className="bg-primary/20 border-b border-primary/40 px-4 py-2 flex items-center justify-center space-x-2 text-xs font-semibold text-primary transition-all animate-fade-in">
            <Check className="w-3.5 h-3.5" />
            <span>{toastMessage}</span>
          </div>
        )}

        {/* Dynamic Views: Main Menu vs Playlist Picker */}
        <div className="overflow-y-auto max-h-[60vh] py-2 divide-y divide-border/20">
          {view === "menu" ? (
            <div className="flex flex-col py-1">
              {/* 1. Toggle Favorite */}
              <button
                onClick={handleToggleFav}
                className="w-full flex items-center space-x-3.5 px-4 py-3 text-left hover:bg-surface-light/60 active:bg-surface-light transition-colors"
              >
                <Heart
                  className={`w-5 h-5 ${
                    track.is_favorite
                      ? "fill-primary text-primary"
                      : "text-content-secondary"
                  }`}
                />
                <div className="flex flex-col min-w-0">
                  <span className="text-sm font-medium text-content-primary">
                    {track.is_favorite
                      ? "Remove from Favorites"
                      : "Add to Favorites"}
                  </span>
                  <span className="text-xs text-content-muted">
                    {track.is_favorite
                      ? "Saved in your library"
                      : "Save to your liked songs"}
                  </span>
                </div>
              </button>

              {/* 2. Add to Playlist */}
              <button
                onClick={handleOpenPlaylists}
                className="w-full flex items-center space-x-3.5 px-4 py-3 text-left hover:bg-surface-light/60 active:bg-surface-light transition-colors"
              >
                <ListPlus className="w-5 h-5 text-content-secondary" />
                <div className="flex flex-col min-w-0">
                  <span className="text-sm font-medium text-content-primary">
                    Add to Playlist
                  </span>
                  <span className="text-xs text-content-muted">
                    Save to custom playlist
                  </span>
                </div>
              </button>

              {/* 3. Add to Queue */}
              <button
                onClick={handleAddToQueue}
                className="w-full flex items-center space-x-3.5 px-4 py-3 text-left hover:bg-surface-light/60 active:bg-surface-light transition-colors"
              >
                <ListEnd className="w-5 h-5 text-content-secondary" />
                <div className="flex flex-col min-w-0">
                  <span className="text-sm font-medium text-content-primary">
                    Add to Queue
                  </span>
                  <span className="text-xs text-content-muted">
                    Play at the end of current queue
                  </span>
                </div>
              </button>

              {/* 4. Play Next */}
              <button
                onClick={handlePlayNext}
                className="w-full flex items-center space-x-3.5 px-4 py-3 text-left hover:bg-surface-light/60 active:bg-surface-light transition-colors"
              >
                <PlaySquare className="w-5 h-5 text-content-secondary" />
                <div className="flex flex-col min-w-0">
                  <span className="text-sm font-medium text-content-primary">
                    Play Next
                  </span>
                  <span className="text-xs text-content-muted">
                    Insert directly after current song
                  </span>
                </div>
              </button>

              {/* 5. Go to Album */}
              {track.album_title && (
                <button
                  onClick={handleFilterByAlbum}
                  className="w-full flex items-center space-x-3.5 px-4 py-3 text-left hover:bg-surface-light/60 active:bg-surface-light transition-colors"
                >
                  <Disc className="w-5 h-5 text-content-secondary" />
                  <div className="flex flex-col min-w-0">
                    <span className="text-sm font-medium text-content-primary">
                      Go to Album
                    </span>
                    <span className="text-xs text-content-muted truncate">
                      {track.album_title}
                    </span>
                  </div>
                </button>
              )}

              {/* 6. Go to Artist */}
              {track.artist_name && (
                <button
                  onClick={handleFilterByArtist}
                  className="w-full flex items-center space-x-3.5 px-4 py-3 text-left hover:bg-surface-light/60 active:bg-surface-light transition-colors"
                >
                  <User className="w-5 h-5 text-content-secondary" />
                  <div className="flex flex-col min-w-0">
                    <span className="text-sm font-medium text-content-primary">
                      Go to Artist
                    </span>
                    <span className="text-xs text-content-muted truncate">
                      {track.artist_name}
                    </span>
                  </div>
                </button>
              )}

              {/* 7. Share Track */}
              <button
                onClick={handleShare}
                className="w-full flex items-center space-x-3.5 px-4 py-3 text-left hover:bg-surface-light/60 active:bg-surface-light transition-colors"
              >
                <Share2 className="w-5 h-5 text-content-secondary" />
                <div className="flex flex-col min-w-0">
                  <span className="text-sm font-medium text-content-primary">
                    Share Track
                  </span>
                  <span className="text-xs text-content-muted">
                    Copy Telegram Mini App share link
                  </span>
                </div>
              </button>
            </div>
          ) : (
            /* Playlist Picker View */
            <div className="flex flex-col py-1">
              <div className="flex items-center space-x-2 px-4 py-2 border-b border-border/30">
                <button
                  onClick={() => {
                    haptic("light");
                    setView("menu");
                  }}
                  className="p-1 rounded-full text-content-secondary hover:text-content-primary hover:bg-surface-light transition-colors"
                >
                  <ArrowLeft className="w-4 h-4" />
                </button>
                <span className="text-sm font-semibold text-content-primary">
                  Select a Playlist
                </span>
              </div>

              {isLoadingPlaylists ? (
                <div className="flex flex-col items-center justify-center py-8 space-y-2">
                  <Loader2 className="w-5 h-5 text-primary animate-spin" />
                  <span className="text-xs text-content-muted">
                    Loading your playlists...
                  </span>
                </div>
              ) : playlists.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-8 text-center px-4 space-y-2">
                  <Music className="w-8 h-8 text-content-muted/50" />
                  <span className="text-xs text-content-secondary">
                    No custom playlists found.
                  </span>
                  <span className="text-[11px] text-content-muted">
                    Create playlists from your Library or Profile.
                  </span>
                </div>
              ) : (
                playlists.map((pl) => {
                  const isActing = playlistActionStatus?.id === pl.id;
                  const isDone = isActing && playlistActionStatus.status === "done";
                  return (
                    <button
                      key={pl.id}
                      disabled={isActing}
                      onClick={() => handleAddTrackToPlaylist(pl)}
                      className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-surface-light/60 active:bg-surface-light transition-colors"
                    >
                      <div className="flex flex-col min-w-0 pr-3">
                        <span className="text-sm font-medium text-content-primary truncate">
                          {pl.name}
                        </span>
                        <span className="text-xs text-content-muted">
                          {pl.track_count ?? 0} tracks
                        </span>
                      </div>

                      {isActing ? (
                        isDone ? (
                          <Check className="w-4 h-4 text-primary" />
                        ) : (
                          <Loader2 className="w-4 h-4 text-primary animate-spin" />
                        )
                      ) : (
                        <ListPlus className="w-4 h-4 text-content-muted" />
                      )}
                    </button>
                  );
                })
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
