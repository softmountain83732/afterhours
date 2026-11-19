"use client";

import React, { useState } from "react";
import {
  ChevronDown,
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Shuffle,
  Repeat,
  Repeat1,
  Heart,
  ListMusic,
  Sliders,
  Radio,
  X,
  Trash2,
  ChevronUp,
  Music2,
} from "lucide-react";
import { usePlayer } from "@/contexts/player-context";
import { useTelegram } from "@/contexts/telegram-context";
import { AudioQuality } from "@/lib/types";

function formatTime(seconds: number): string {
  if (isNaN(seconds) || seconds < 0) return "0:00";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs < 10 ? "0" : ""}${secs}`;
}

export const FullPlayer: React.FC = () => {
  const {
    currentTrack,
    isPlaying,
    currentTime,
    duration,
    togglePlay,
    seek,
    next,
    previous,
    isShuffle,
    toggleShuffle,
    repeatMode,
    toggleRepeat,
    quality,
    setQuality,
    isFullPlayerOpen,
    setIsFullPlayerOpen,
    toggleFavorite,
    queue,
    playTrack,
    removeFromQueue,
    reorderQueue,
    clearQueue,
  } = usePlayer();

  const { haptic } = useTelegram();
  const [showQueue, setShowQueue] = useState(false);
  const [showQualityMenu, setShowQualityMenu] = useState(false);
  const [isSeeking, setIsSeeking] = useState(false);
  const [seekValue, setSeekValue] = useState(0);

  if (!isFullPlayerOpen || !currentTrack) return null;

  const artworkFull = currentTrack.artwork_url || currentTrack.artwork_thumb_url || "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=800&q=80";
  const artworkThumb = currentTrack.artwork_thumb_url || artworkFull;

  const progressPercent = duration > 0 ? ((isSeeking ? seekValue : currentTime) / duration) * 100 : 0;

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setIsSeeking(true);
    setSeekValue(parseFloat(e.target.value));
  };

  const handleSliderCommit = () => {
    setIsSeeking(false);
    seek(seekValue);
  };

  return (
    <div className="fixed inset-0 z-50 flex flex-col justify-between overflow-hidden bg-background animate-slide-up text-content-primary">
      {/* ─── BLURRED BACKGROUND ────────────────────────────────────────────── */}
      <div className="absolute inset-0 z-0 overflow-hidden pointer-events-none">
        <img
          src={artworkThumb}
          alt=""
          className="w-full h-full object-cover scale-125 filter blur-[40px] opacity-35 transform transition-all duration-700 ease-out"
        />
        {/* Dark vignette overlay for contrast and luxury aesthetic */}
        <div className="absolute inset-0 bg-gradient-to-b from-[#08080c]/80 via-[#08080c]/60 to-[#08080c]/95" />
      </div>

      {/* ─── TOP BAR ───────────────────────────────────────────────────────── */}
      <header className="relative z-10 flex items-center justify-between px-6 pt-[max(env(safe-area-inset-top),16px)] pb-4">
        <button
          onClick={() => {
            haptic("light");
            setIsFullPlayerOpen(false);
          }}
          className="w-10 h-10 flex items-center justify-center rounded-full bg-white/5 hover:bg-white/10 text-content-secondary hover:text-content-primary transition-all active:scale-95"
          aria-label="Close player"
        >
          <ChevronDown className="w-6 h-6" />
        </button>

        <div className="flex flex-col items-center">
          <span className="text-[10px] uppercase font-bold tracking-widest text-primary">
            Playing From Library
          </span>
          <span className="text-xs font-semibold text-content-secondary max-w-[200px] truncate">
            {currentTrack.album_title || "Afterhours"}
          </span>
        </div>

        <button
          onClick={() => setShowQualityMenu(!showQualityMenu)}
          className={`px-2.5 py-1 rounded-full text-[11px] font-semibold uppercase tracking-wider border transition-all ${
            quality === "high"
              ? "border-accent text-accent bg-accent/10"
              : "border-border text-content-secondary bg-white/5"
          }`}
        >
          {quality}
        </button>
      </header>

      {/* ─── QUALITY SELECTOR DROPDOWN ─────────────────────────────────────── */}
      {showQualityMenu && (
        <div className="absolute top-16 right-6 z-20 w-44 bg-surface-hover/95 backdrop-blur-xl border border-border rounded-xl shadow-2xl p-1.5 animate-fade-in">
          {(["saver", "standard", "high"] as AudioQuality[]).map((q) => (
            <button
              key={q}
              onClick={() => {
                haptic("light");
                setQuality(q);
                setShowQualityMenu(false);
              }}
              className={`w-full flex items-center justify-between px-3 py-2 text-xs font-medium rounded-lg capitalize transition-colors ${
                quality === q ? "bg-primary text-white" : "text-content-secondary hover:bg-white/5"
              }`}
            >
              <span>{q === "high" ? "High (320k)" : q === "saver" ? "Data Saver (128k)" : "Standard (256k)"}</span>
              {quality === q && <span className="w-1.5 h-1.5 rounded-full bg-white ml-2" />}
            </button>
          ))}
        </div>
      )}

      {/* ─── FOREGROUND ALBUM COVER ────────────────────────────────────────── */}
      <main className="relative z-10 flex-1 flex flex-col items-center justify-center px-8 my-auto">
        <div className="relative w-full max-w-[340px] aspect-square rounded-2xl overflow-hidden shadow-2xl border border-white/10 group">
          <img
            src={artworkFull}
            alt={currentTrack.title}
            className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
          />
        </div>

        {/* Song Info & Favorite */}
        <div className="w-full max-w-[340px] flex items-center justify-between mt-8">
          <div className="flex flex-col min-w-0 pr-4">
            <h1 className="text-2xl font-bold tracking-tight text-content-primary truncate">
              {currentTrack.title}
            </h1>
            <p className="text-base font-medium text-content-secondary truncate mt-0.5">
              {currentTrack.artist_name}
            </p>
          </div>

          <button
            onClick={() => toggleFavorite(currentTrack)}
            aria-label="Favorite"
            className="w-11 h-11 flex items-center justify-center rounded-full bg-white/5 hover:bg-white/10 transition-all active:scale-90"
          >
            <Heart
              className={`w-6 h-6 transition-colors ${
                currentTrack.is_favorite
                  ? "fill-primary text-primary"
                  : "text-content-secondary hover:text-content-primary"
              }`}
            />
          </button>
        </div>
      </main>

      {/* ─── PLAYER CONTROLS & PROGRESS ─────────────────────────────────────── */}
      <footer className="relative z-10 w-full max-w-[380px] mx-auto px-6 pb-[max(env(safe-area-inset-bottom),24px)] flex flex-col space-y-6">
        {/* Progress Bar */}
        <div className="flex flex-col space-y-2">
          <div className="relative w-full flex items-center">
            <input
              type="range"
              min={0}
              max={duration || 100}
              step={0.1}
              value={isSeeking ? seekValue : currentTime}
              onChange={handleSliderChange}
              onMouseUp={handleSliderCommit}
              onTouchEnd={handleSliderCommit}
              className="w-full h-1.5 bg-border/60 rounded-lg appearance-none cursor-pointer accent-primary"
            />
          </div>
          <div className="flex justify-between text-xs font-mono text-content-muted">
            <span>{formatTime(isSeeking ? seekValue : currentTime)}</span>
            <span>{formatTime(duration)}</span>
          </div>
        </div>

        {/* Primary Controls */}
        <div className="flex items-center justify-between px-2">
          {/* Shuffle */}
          <button
            onClick={toggleShuffle}
            aria-label="Shuffle"
            className={`w-10 h-10 flex items-center justify-center transition-colors ${
              isShuffle ? "text-primary" : "text-content-muted hover:text-content-primary"
            }`}
          >
            <Shuffle className="w-5 h-5" />
          </button>

          {/* Previous */}
          <button
            onClick={previous}
            aria-label="Previous"
            className="w-12 h-12 flex items-center justify-center text-content-primary hover:text-white transition-all active:scale-90"
          >
            <SkipBack className="w-7 h-7 fill-current" />
          </button>

          {/* Play/Pause */}
          <button
            onClick={togglePlay}
            aria-label={isPlaying ? "Pause" : "Play"}
            className="w-16 h-16 flex items-center justify-center rounded-full bg-primary hover:bg-primary-hover text-white shadow-xl shadow-primary/30 transition-all active:scale-95"
          >
            {isPlaying ? (
              <Pause className="w-7 h-7 fill-current" />
            ) : (
              <Play className="w-7 h-7 fill-current ml-1" />
            )}
          </button>

          {/* Next */}
          <button
            onClick={next}
            aria-label="Next"
            className="w-12 h-12 flex items-center justify-center text-content-primary hover:text-white transition-all active:scale-90"
          >
            <SkipForward className="w-7 h-7 fill-current" />
          </button>

          {/* Repeat */}
          <button
            onClick={toggleRepeat}
            aria-label={`Repeat mode: ${repeatMode}`}
            title={`Repeat: ${repeatMode === "off" ? "Off" : repeatMode === "all" ? "Repeat All" : "Repeat One"}`}
            className={`relative w-10 h-10 flex items-center justify-center transition-colors active:scale-90 ${
              repeatMode !== "off" ? "text-primary" : "text-content-muted hover:text-content-primary"
            }`}
          >
            {repeatMode === "one" ? (
              <Repeat1 className="w-5 h-5" />
            ) : (
              <Repeat className="w-5 h-5" />
            )}
            {repeatMode !== "off" && (
              <span className="absolute bottom-1 w-1 h-1 rounded-full bg-primary" />
            )}
          </button>
        </div>

        {/* Secondary Bottom Controls */}
        <div className="flex items-center justify-between pt-2 border-t border-border/40 text-content-secondary">
          <button
            onClick={() => setShowQueue(!showQueue)}
            className="flex items-center space-x-2 text-xs font-medium hover:text-content-primary transition-colors active:scale-95"
          >
            <ListMusic className="w-4 h-4" />
            <span>Up Next ({queue.length})</span>
          </button>

          <span className="text-[11px] font-mono tracking-tight text-content-muted">
            {currentTrack.codec.toUpperCase()} {currentTrack.bitrate ? `${Math.round(currentTrack.bitrate / 1000)}k` : ""}
          </span>
        </div>
      </footer>

      {/* ─── QUEUE DRAWER MODAL ────────────────────────────────────────────── */}
      {showQueue && (
        <div className="absolute inset-0 z-30 bg-surface/98 backdrop-blur-2xl flex flex-col animate-slide-up">
          <div className="flex items-center justify-between p-6 border-b border-border">
            <div className="flex items-center space-x-3">
              <h2 className="text-lg font-bold text-content-primary">Playback Queue</h2>
              <span className="text-xs px-2 py-0.5 rounded-full bg-white/5 text-content-secondary font-mono">
                {queue.length}
              </span>
            </div>
            <div className="flex items-center space-x-2">
              {queue.length > 1 && (
                <button
                  onClick={() => {
                    haptic("medium");
                    clearQueue();
                  }}
                  className="px-2.5 py-1 text-xs text-red-400 hover:text-red-300 font-medium transition-colors"
                >
                  Clear
                </button>
              )}
              <button
                onClick={() => setShowQueue(false)}
                className="w-8 h-8 flex items-center justify-center rounded-full bg-white/5 hover:bg-white/10 text-content-secondary"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-2">
            {queue.map((t, index) => {
              const isCurrent = t.id === currentTrack.id;
              return (
                <div
                  key={`${t.id}-${index}`}
                  className={`group flex items-center justify-between p-2.5 rounded-xl transition-all ${
                    isCurrent
                      ? "bg-primary/20 border border-primary/40 text-primary"
                      : "hover:bg-white/5 text-content-primary"
                  }`}
                >
                  <div
                    onClick={() => {
                      haptic("light");
                      playTrack(t);
                      setShowQueue(false);
                    }}
                    className="flex items-center space-x-3 overflow-hidden flex-1 cursor-pointer pr-2"
                  >
                    <div className="relative w-10 h-10 flex-shrink-0 rounded-lg overflow-hidden bg-surface-light border border-border/40">
                      <img
                        src={t.artwork_thumb_url || t.artwork_url || artworkThumb}
                        alt=""
                        className="w-full h-full object-cover"
                      />
                      {isCurrent && isPlaying && (
                        <div className="absolute inset-0 bg-black/40 flex items-center justify-center">
                          <div className="flex items-end space-x-0.5 h-2.5">
                            <span className="w-0.5 h-2.5 bg-primary animate-pulse" />
                            <span className="w-0.5 h-1.5 bg-primary animate-pulse delay-75" />
                            <span className="w-0.5 h-2.5 bg-primary animate-pulse delay-150" />
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="flex flex-col min-w-0">
                      <div className="flex items-center space-x-1.5">
                        <span className="text-sm font-semibold truncate">{t.title}</span>
                        {isCurrent && (
                          <span className="text-[10px] font-bold uppercase tracking-wider text-primary px-1.5 py-0.2 rounded bg-primary/20">
                            Now
                          </span>
                        )}
                      </div>
                      <span className="text-xs text-content-secondary truncate">{t.artist_name}</span>
                    </div>
                  </div>

                  <div className="flex items-center space-x-1">
                    <span className="text-xs font-mono text-content-muted mr-1">
                      {formatTime(t.duration_ms / 1000)}
                    </span>

                    {/* Move Up */}
                    {index > 0 && !isCurrent && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          reorderQueue(index, index - 1);
                        }}
                        title="Move up"
                        className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-white/10 text-content-muted hover:text-content-primary transition-colors"
                      >
                        <ChevronUp className="w-3.5 h-3.5" />
                      </button>
                    )}

                    {/* Move Down */}
                    {index < queue.length - 1 && !isCurrent && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          reorderQueue(index, index + 1);
                        }}
                        title="Move down"
                        className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-white/10 text-content-muted hover:text-content-primary transition-colors"
                      >
                        <ChevronDown className="w-3.5 h-3.5" />
                      </button>
                    )}

                    {/* Remove from queue */}
                    {!isCurrent && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          removeFromQueue(index);
                        }}
                        title="Remove from queue"
                        className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-red-500/10 text-content-muted hover:text-red-400 transition-colors"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
