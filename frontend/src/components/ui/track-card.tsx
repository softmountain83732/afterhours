"use client";

import React from "react";
import { Play, Pause, Heart, MoreVertical } from "lucide-react";
import { Track } from "@/lib/types";
import { usePlayer } from "@/contexts/player-context";
import { useTelegram } from "@/contexts/telegram-context";

interface TrackCardProps {
  track: Track;
  index?: number;
  onSelect?: () => void;
  showCover?: boolean;
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
}) => {
  const { currentTrack, isPlaying, playTrack, togglePlay, toggleFavorite } = usePlayer();
  const { haptic } = useTelegram();

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

  const artwork = track.artwork_thumb_url || track.artwork_url || "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=300&q=80";

  return (
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
          <div className="relative w-11 h-11 flex-shrink-0 rounded-lg overflow-hidden bg-surface-light border border-border/40">
            <img src={artwork} alt="" className="w-full h-full object-cover" />
            {isCurrentlyPlaying && (
              <div className="absolute inset-0 bg-black/40 flex items-center justify-center">
                <div className="flex items-end space-x-0.5 h-3">
                  <span className="w-0.5 h-3 bg-primary animate-pulse" />
                  <span className="w-0.5 h-2 bg-primary animate-pulse delay-75" />
                  <span className="w-0.5 h-3 bg-primary animate-pulse delay-150" />
                </div>
              </div>
            )}
          </div>
        )}

        <div className="flex flex-col min-w-0">
          <span
            className={`text-sm font-semibold truncate ${
              isCurrent ? "text-primary" : "text-content-primary"
            }`}
          >
            {track.title}
          </span>
          <span className="text-xs text-content-secondary truncate">
            {track.artist_name}
          </span>
        </div>
      </div>

      <div className="flex items-center space-x-2.5" onClick={(e) => e.stopPropagation()}>
        <span className="text-xs font-mono text-content-muted">
          {formatDuration(track.duration_ms)}
        </span>

        <button
          onClick={() => toggleFavorite(track)}
          aria-label="Favorite"
          className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-white/5 active:scale-90 transition-all"
        >
          <Heart
            className={`w-4 h-4 transition-colors ${
              track.is_favorite
                ? "fill-primary text-primary"
                : "text-content-muted hover:text-content-primary"
            }`}
          />
        </button>
      </div>
    </div>
  );
};
