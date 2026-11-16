"use client";

import React from "react";
import Image from "next/image";
import { Play, Pause, SkipForward } from "lucide-react";
import { usePlayer } from "@/contexts/player-context";
import { useTelegram } from "@/contexts/telegram-context";

export const MiniPlayer: React.FC = () => {
  const { currentTrack, isPlaying, togglePlay, next, currentTime, duration, setIsFullPlayerOpen } = usePlayer();
  const { haptic } = useTelegram();

  if (!currentTrack) return null;

  const progressPercent = duration > 0 ? (currentTime / duration) * 100 : 0;
  const artwork = currentTrack.artwork_thumb_url || currentTrack.artwork_url || "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=300&q=80";

  return (
    <div
      onClick={() => {
        haptic("light");
        setIsFullPlayerOpen(true);
      }}
      className="fixed bottom-[calc(60px+max(env(safe-area-inset-bottom),8px))] left-2 right-2 z-30 max-w-md mx-auto bg-surface-hover/95 backdrop-blur-xl border border-border/80 rounded-2xl shadow-2xl cursor-pointer overflow-hidden transition-all duration-200 active:scale-[0.99]"
    >
      {/* Progress line */}
      <div className="w-full bg-border/40 h-[2.5px]">
        <div
          className="bg-primary h-full transition-all duration-200"
          style={{ width: `${Math.min(100, Math.max(0, progressPercent))}%` }}
        />
      </div>

      <div className="flex items-center justify-between p-2.5">
        {/* Track Artwork & Info */}
        <div className="flex items-center space-x-3 overflow-hidden flex-1 pr-2">
          <div className="relative w-11 h-11 flex-shrink-0 rounded-xl overflow-hidden shadow-md bg-surface-light border border-border/40">
            <img
              src={artwork}
              alt={currentTrack.title}
              className="w-full h-full object-cover"
            />
          </div>
          <div className="flex flex-col min-w-0">
            <span className="text-sm font-semibold text-content-primary truncate tracking-tight">
              {currentTrack.title}
            </span>
            <span className="text-xs text-content-secondary truncate">
              {currentTrack.artist_name}
            </span>
          </div>
        </div>

        {/* Playback Controls */}
        <div className="flex items-center space-x-1" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={togglePlay}
            aria-label={isPlaying ? "Pause" : "Play"}
            className="w-9 h-9 flex items-center justify-center rounded-full bg-content-primary/10 hover:bg-content-primary/20 text-content-primary transition-all active:scale-90"
          >
            {isPlaying ? (
              <Pause className="w-4 h-4 fill-current" />
            ) : (
              <Play className="w-4 h-4 fill-current ml-0.5" />
            )}
          </button>
          <button
            onClick={next}
            aria-label="Next track"
            className="w-9 h-9 flex items-center justify-center rounded-full text-content-secondary hover:text-content-primary transition-all active:scale-90"
          >
            <SkipForward className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
};
