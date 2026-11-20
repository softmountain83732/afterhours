"use client";

import React, { useState } from "react";
import { Heart, MoreHorizontal } from "lucide-react";
import { Track } from "@/lib/types";
import { usePlayer } from "@/contexts/player-context";
import { useTelegram } from "@/contexts/telegram-context";
import { TrackContextMenu } from "./track-context-menu";
import { TrackArtwork } from "./track-artwork";

interface TrackCardProps {
  track: Track;
  index?: number;
  onSelect?: () => void;
  showCover?: boolean;
  onOpenContextMenu?: (track: Track) => void;
  onFilterArtist?: (artistName: string) => void;
  onFilterAlbum?: (albumTitle: string) => void;
}

function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds < 10 ? "0" : ""}${seconds}`;
}

export const TrackCard: React.FC<TrackCardProps> = ({
  track,
  index,
  onSelect,
  showCover = true,
  onOpenContextMenu,
  onFilterArtist,
  onFilterAlbum,
}) => {
  const { currentTrack, isPlaying, playTrack, togglePlay } = usePlayer();
  const { haptic } = useTelegram();
  const [internalMenuOpen, setInternalMenuOpen] = useState(false);

  const isCurrent = currentTrack?.id === track.id;
  const isCurrentlyPlaying = isCurrent && isPlaying;

  const handleCardClick = () => {
    haptic("light");
    if (onSelect) {
      onSelect();
    } else {
      if (isCurrent) {
        togglePlay();
      } else {
        playTrack(track);
      }
    }
  };

  const handleMenuClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    haptic("light");
    if (onOpenContextMenu) {
      onOpenContextMenu(track);
    } else {
      setInternalMenuOpen(true);
    }
  };

  return (
    <>
      <div
        onClick={handleCardClick}
        className={`group flex items-center justify-between p-2.5 rounded-xl transition-all cursor-pointer select-none active:scale-[0.99] ${
          isCurrent ? "bg-surface-light border border-border" : "hover:bg-surface/60"
        }`}
      >
        <div className="flex items-center space-x-3 min-w-0 flex-1 pr-2">
          {index !== undefined && !showCover && (
            <span className="w-5 text-center text-xs font-mono text-content-muted">
              {index + 1}
            </span>
          )}

          {showCover && (
            <div className="relative w-11 h-11 flex-shrink-0 rounded-lg overflow-hidden border border-border/40">
              <TrackArtwork src={track.artwork_thumb_url || track.artwork_url} alt={track.title}>
                {isCurrentlyPlaying && (
                  <div className="absolute inset-0 bg-black/40 flex items-center justify-center">
                    <div className="flex items-end space-x-0.5 h-3">
                      <span className="w-0.5 h-3 bg-primary animate-pulse" />
                      <span className="w-0.5 h-2 bg-primary animate-pulse delay-75" />
                      <span className="w-0.5 h-3 bg-primary animate-pulse delay-150" />
                    </div>
                  </div>
                )}
              </TrackArtwork>
            </div>
          )}

          <div className="flex flex-col min-w-0">
            <div className="flex items-center space-x-1.5 min-w-0">
              <span
                className={`text-sm font-semibold truncate ${
                  isCurrent ? "text-primary" : "text-content-primary"
                }`}
              >
                {track.title}
              </span>
              {track.is_favorite && (
                <Heart className="w-3 h-3 fill-primary text-primary flex-shrink-0" />
              )}
            </div>
            <span className="text-xs text-content-secondary truncate mt-0.5">
              {track.artist_name}
            </span>
          </div>
        </div>

        <div className="flex items-center space-x-2.5" onClick={(e) => e.stopPropagation()}>
          <span className="text-xs font-mono text-content-muted">
            {formatDuration(track.duration_ms)}
          </span>

          <button
            onClick={handleMenuClick}
            aria-label="Track options"
            className="w-8 h-8 flex items-center justify-center rounded-full text-content-muted hover:text-content-primary hover:bg-white/5 active:scale-90 transition-all"
          >
            <MoreHorizontal className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Internal context menu fallback if no external coordinator provided */}
      {!onOpenContextMenu && (
        <TrackContextMenu
          track={track}
          isOpen={internalMenuOpen}
          onClose={() => setInternalMenuOpen(false)}
          onFilterArtist={onFilterArtist}
          onFilterAlbum={onFilterAlbum}
        />
      )}
    </>
  );
};
