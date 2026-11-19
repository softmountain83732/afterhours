"use client";

import React, { useState, useEffect, useRef } from "react";
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
  RotateCcw,
} from "lucide-react";
import { usePlayer } from "@/contexts/player-context";
import { useTelegram } from "@/contexts/telegram-context";
import { AudioQuality, Track } from "@/lib/types";

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
    insertTrackAt,
    reorderQueue,
    clearQueue,
  } = usePlayer();

  const { haptic } = useTelegram();
  const [showQueue, setShowQueue] = useState(false);
  const [showQualityMenu, setShowQualityMenu] = useState(false);
  const [isSeeking, setIsSeeking] = useState(false);
  const [seekValue, setSeekValue] = useState(0);

  // Queue drag & swipe states
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);
  const [swipedTrackId, setSwipedTrackId] = useState<string | null>(null);
  const [undoItem, setUndoItem] = useState<{ track: Track; index: number } | null>(null);
  const undoTimerRef = useRef<NodeJS.Timeout | null>(null);

  // Touch gesture tracker
  const touchStartX = useRef<number>(0);
  const touchStartY = useRef<number>(0);
  const isHorizontalSwipe = useRef<boolean | null>(null);

  // Telegram BackButton and popstate integration
  useEffect(() => {
    if (!isFullPlayerOpen) {
      setShowQueue(false);
      return;
    }

    const tg = typeof window !== "undefined" ? (window as any).Telegram?.WebApp : null;
    const backBtn = tg?.BackButton;

    const handleBack = () => {
      haptic("light");
      if (showQueue) {
        setShowQueue(false);
      } else {
        setIsFullPlayerOpen(false);
      }
    };

    if (backBtn) {
      backBtn.show();
      backBtn.onClick(handleBack);
    }

    // Push history state to intercept Android hardware back button
    window.history.pushState({ fullPlayerOpen: true }, "");

    const handlePopState = () => {
      if (showQueue) {
        setShowQueue(false);
      } else {
        setIsFullPlayerOpen(false);
      }
    };

    window.addEventListener("popstate", handlePopState);

    return () => {
      if (backBtn) {
        backBtn.offClick(handleBack);
        backBtn.hide();
      }
      window.removeEventListener("popstate", handlePopState);
    };
  }, [isFullPlayerOpen, showQueue, setIsFullPlayerOpen, haptic]);

  if (!isFullPlayerOpen || !currentTrack) return null;

  const artworkFull =
    currentTrack.artwork_url ||
    currentTrack.artwork_thumb_url ||
    "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=800&q=80";
  const artworkThumb = currentTrack.artwork_thumb_url || artworkFull;

  const progressPercent =
    duration > 0 ? ((isSeeking ? seekValue : currentTime) / duration) * 100 : 0;

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setIsSeeking(true);
    setSeekValue(parseFloat(e.target.value));
  };

  const handleSliderCommit = () => {
    setIsSeeking(false);
    seek(seekValue);
  };

  const handleRemoveTrack = (t: Track, index: number) => {
    haptic("medium");
    removeFromQueue(index);
    setSwipedTrackId(null);

    // Save for undo
    setUndoItem({ track: t, index });
    if (undoTimerRef.current) clearTimeout(undoTimerRef.current);
    undoTimerRef.current = setTimeout(() => {
      setUndoItem(null);
    }, 4500);
  };

  const handleUndoRemove = () => {
    if (!undoItem) return;
    haptic("medium");
    insertTrackAt(undoItem.track, undoItem.index);
    setUndoItem(null);
    if (undoTimerRef.current) clearTimeout(undoTimerRef.current);
  };

  // Drag-and-drop handlers for queue rows
  const handleDragStart = (e: React.DragEvent, index: number) => {
    setDraggedIndex(index);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    if (dragOverIndex !== index) {
      setDragOverIndex(index);
    }
  };

  const handleDrop = (e: React.DragEvent, targetIndex: number) => {
    e.preventDefault();
    if (draggedIndex !== null && draggedIndex !== targetIndex) {
      reorderQueue(draggedIndex, targetIndex);
    }
    setDraggedIndex(null);
    setDragOverIndex(null);
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
        <div className="absolute inset-0 bg-gradient-to-b from-[#08080c]/80 via-[#08080c]/60 to-[#08080c]/95" />
      </div>

      {/* ─── TOP BAR ───────────────────────────────────────────────────────── */}
      <header className="relative z-10 flex items-center justify-between px-6 pt-[max(env(safe-area-inset-top),16px)] pb-4">
        <button
          onClick={() => {
            haptic("light");
            setIsFullPlayerOpen(false);
          }}
          className="w-10 h-10 flex items-center justify-center rounded-full bg-surface-light/40 border border-border/40 hover:bg-surface-light text-content-primary transition-colors"
          aria-label="Collapse player"
        >
          <ChevronDown className="w-5 h-5" />
        </button>

        <div className="flex flex-col items-center">
          <span className="text-[10px] font-bold tracking-widest uppercase text-content-secondary">
            Playing From
          </span>
          <span className="text-xs font-semibold text-content-primary max-w-[200px] truncate">
            {currentTrack.album_title || "Afterhours Vault"}
          </span>
        </div>

        <button
          onClick={() => {
            haptic("light");
            setShowQualityMenu(!showQualityMenu);
          }}
          className={`w-10 h-10 flex items-center justify-center rounded-full border transition-colors ${
            showQualityMenu
              ? "bg-primary border-primary text-white"
              : "bg-surface-light/40 border-border/40 text-content-primary hover:bg-surface-light"
          }`}
          aria-label="Audio quality"
        >
          <Sliders className="w-4 h-4" />
        </button>
      </header>

      {/* Quality Picker Overlay */}
      {showQualityMenu && (
        <div className="relative z-20 mx-6 p-3 rounded-2xl bg-surface/95 border border-border backdrop-blur-xl animate-fade-in shadow-2xl">
          <div className="flex items-center justify-between mb-2 px-1">
            <span className="text-xs font-bold text-content-secondary uppercase tracking-wider">
              Audio Quality
            </span>
            <span className="text-[11px] font-mono text-primary font-semibold">
              {currentTrack.codec.toUpperCase()}
            </span>
          </div>
          <div className="grid grid-cols-3 gap-2">
            {(["saver", "standard", "high"] as AudioQuality[]).map((q) => (
              <button
                key={q}
                onClick={() => {
                  haptic("light");
                  setQuality(q);
                  setShowQualityMenu(false);
                }}
                className={`py-2 px-3 rounded-xl text-xs font-semibold capitalize transition-all ${
                  quality === q
                    ? "bg-primary text-white shadow-md shadow-primary/30"
                    : "bg-surface-light/60 hover:bg-surface-light text-content-secondary hover:text-content-primary"
                }`}
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* ─── ALBUM ARTWORK ─────────────────────────────────────────────────── */}
      <div className="relative z-10 flex-1 flex items-center justify-center px-8 py-4">
        <div className="relative w-full aspect-square max-w-[340px] max-h-[340px] rounded-3xl overflow-hidden shadow-2xl border border-border/40">
          <img
            src={artworkFull}
            alt={currentTrack.title}
            className="w-full h-full object-cover transition-transform duration-500 hover:scale-105"
            onError={(e) => {
              (e.target as HTMLImageElement).src =
                "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=800&q=80";
            }}
          />
        </div>
      </div>

      {/* ─── TRACK METADATA ────────────────────────────────────────────────── */}
      <div className="relative z-10 px-8 py-2">
        <div className="flex items-center justify-between">
          <div className="flex-1 min-w-0 pr-4">
            <h1 className="text-xl sm:text-2xl font-black text-content-primary tracking-tight truncate">
              {currentTrack.title}
            </h1>
            <p className="text-sm font-medium text-content-secondary truncate mt-1">
              {currentTrack.artist_name}
            </p>
          </div>

          <button
            onClick={() => {
              haptic("medium");
              toggleFavorite(currentTrack);
            }}
            className="w-12 h-12 flex-shrink-0 flex items-center justify-center rounded-full hover:bg-white/5 active:scale-90 transition-all"
            aria-label="Favorite"
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
      </div>

      {/* ─── SEEK / PROGRESS BAR ───────────────────────────────────────────── */}
      <div className="relative z-10 px-8 py-2">
        <div className="relative w-full h-6 flex items-center group cursor-pointer">
          <input
            type="range"
            min={0}
            max={duration || 100}
            step={0.1}
            value={isSeeking ? seekValue : currentTime}
            onChange={handleSliderChange}
            onMouseUp={handleSliderCommit}
            onTouchEnd={handleSliderCommit}
            className="w-full h-1 bg-surface-light rounded-full appearance-none cursor-pointer accent-primary"
          />
        </div>

        <div className="flex justify-between text-[11px] font-mono text-content-muted mt-1">
          <span>{formatTime(isSeeking ? seekValue : currentTime)}</span>
          <span>{formatTime(duration)}</span>
        </div>
      </div>

      {/* ─── PLAYBACK CONTROLS ─────────────────────────────────────────────── */}
      <div className="relative z-10 px-8 py-4 flex items-center justify-between">
        {/* Shuffle Button */}
        <button
          onClick={() => {
            haptic("light");
            toggleShuffle();
          }}
          className={`p-3 rounded-full transition-all ${
            isShuffle
              ? "text-primary bg-primary/10"
              : "text-content-secondary hover:text-content-primary"
          }`}
          aria-label="Shuffle"
        >
          <Shuffle className="w-5 h-5" />
        </button>

        {/* Previous Track */}
        <button
          onClick={() => {
            haptic("light");
            previous();
          }}
          className="p-3 text-content-primary hover:text-white active:scale-90 transition-all"
          aria-label="Previous track"
        >
          <SkipBack className="w-7 h-7 fill-current" />
        </button>

        {/* Play / Pause Primary Button */}
        <button
          onClick={() => {
            haptic("medium");
            togglePlay();
          }}
          className="w-16 h-16 flex items-center justify-center rounded-full bg-primary hover:bg-primary-hover text-white shadow-xl shadow-primary/30 active:scale-95 transition-all"
          aria-label={isPlaying ? "Pause" : "Play"}
        >
          {isPlaying ? (
            <Pause className="w-7 h-7 fill-current" />
          ) : (
            <Play className="w-7 h-7 fill-current ml-1" />
          )}
        </button>

        {/* Next Track */}
        <button
          onClick={() => {
            haptic("light");
            next();
          }}
          className="p-3 text-content-primary hover:text-white active:scale-90 transition-all"
          aria-label="Next track"
        >
          <SkipForward className="w-7 h-7 fill-current" />
        </button>

        {/* Repeat Button with Indicator */}
        <button
          onClick={() => {
            haptic("light");
            toggleRepeat();
          }}
          className={`relative p-3 rounded-full transition-all ${
            repeatMode !== "off"
              ? "text-primary bg-primary/10"
              : "text-content-secondary hover:text-content-primary"
          }`}
          aria-label="Repeat"
        >
          {repeatMode === "one" ? (
            <Repeat1 className="w-5 h-5" />
          ) : (
            <Repeat className="w-5 h-5" />
          )}
          {repeatMode === "all" && (
            <span className="absolute bottom-1 right-2 w-1.5 h-1.5 rounded-full bg-primary" />
          )}
        </button>
      </div>

      {/* ─── BOTTOM BAR ─────────────────────────────────────────────────────── */}
      <footer className="relative z-10 px-8 pb-[max(env(safe-area-inset-bottom),20px)] pt-2 border-t border-border/20">
        <div className="flex items-center justify-between">
          <button
            onClick={() => {
              haptic("light");
              setShowQueue(true);
            }}
            className="flex items-center space-x-2 py-1.5 px-3 rounded-full bg-surface-light/40 border border-border/40 text-xs font-semibold text-content-secondary hover:text-content-primary transition-colors"
          >
            <ListMusic className="w-4 h-4" />
            <span>Up Next ({queue.length})</span>
          </button>

          <span className="text-[11px] font-mono tracking-tight text-content-muted">
            {currentTrack.codec.toUpperCase()}{" "}
            {currentTrack.bitrate ? `${Math.round(currentTrack.bitrate / 1000)}k` : ""}
          </span>
        </div>
      </footer>

      {/* ─── SPOTIFY-STYLE QUEUE DRAWER MODAL ──────────────────────────────── */}
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
              const isSwiped = swipedTrackId === t.id;
              const isDragged = draggedIndex === index;
              const isDragTarget = dragOverIndex === index;

              return (
                <div
                  key={`${t.id}-${index}`}
                  draggable={!isCurrent}
                  onDragStart={(e) => handleDragStart(e, index)}
                  onDragOver={(e) => handleDragOver(e, index)}
                  onDrop={(e) => handleDrop(e, index)}
                  className={`relative overflow-hidden rounded-xl transition-all ${
                    isDragTarget ? "border-t-2 border-primary" : ""
                  } ${isDragged ? "opacity-40" : ""}`}
                >
                  {/* Background Reveal Delete Container */}
                  {!isCurrent && (
                    <div className="absolute inset-y-0 right-0 w-24 bg-red-600 flex items-center justify-center rounded-r-xl">
                      <button
                        onClick={() => handleRemoveTrack(t, index)}
                        className="w-full h-full flex flex-col items-center justify-center text-white space-y-0.5 active:opacity-80"
                      >
                        <Trash2 className="w-4 h-4" />
                        <span className="text-[10px] font-bold uppercase tracking-wider">
                          Delete
                        </span>
                      </button>
                    </div>
                  )}

                  {/* Foreground Queue Row */}
                  <div
                    onTouchStart={(e) => {
                      touchStartX.current = e.touches[0].clientX;
                      touchStartY.current = e.touches[0].clientY;
                      isHorizontalSwipe.current = null;
                    }}
                    onTouchMove={(e) => {
                      if (isCurrent) return;
                      const dx = e.touches[0].clientX - touchStartX.current;
                      const dy = e.touches[0].clientY - touchStartY.current;

                      if (isHorizontalSwipe.current === null) {
                        if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 10) {
                          isHorizontalSwipe.current = true;
                        } else if (Math.abs(dy) > Math.abs(dx)) {
                          isHorizontalSwipe.current = false;
                        }
                      }

                      if (isHorizontalSwipe.current && dx < -40) {
                        setSwipedTrackId(t.id);
                      } else if (isHorizontalSwipe.current && dx > 20) {
                        setSwipedTrackId(null);
                      }
                    }}
                    style={{
                      transform: isSwiped ? "translateX(-88px)" : "translateX(0px)",
                      transition: "transform 0.25s cubic-bezier(0.16, 1, 0.3, 1)",
                    }}
                    className={`relative z-10 flex items-center justify-between p-2.5 rounded-xl bg-surface border ${
                      isCurrent
                        ? "bg-primary/20 border-primary/40 text-primary"
                        : "border-border/40 hover:bg-surface-light text-content-primary"
                    }`}
                  >
                    {/* Track info click to play */}
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
                        <span className="text-xs text-content-secondary truncate">
                          {t.artist_name}
                        </span>
                      </div>
                    </div>

                    {/* Right side: Duration & Spotify Three-Line Drag Handle */}
                    <div className="flex items-center space-x-2.5">
                      <span className="text-xs font-mono text-content-muted">
                        {formatTime(t.duration_ms / 1000)}
                      </span>

                      {!isCurrent && (
                        <div
                          className="w-8 h-8 flex flex-col justify-center items-center space-y-1 cursor-grab active:cursor-grabbing text-content-muted hover:text-content-primary select-none touch-none"
                          title="Drag to reorder"
                        >
                          <span className="w-4 h-0.5 bg-current rounded-full" />
                          <span className="w-4 h-0.5 bg-current rounded-full" />
                          <span className="w-4 h-0.5 bg-current rounded-full" />
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Undo Banner Toast */}
          {undoItem && (
            <div className="p-3 mx-4 mb-4 rounded-xl bg-surface-light border border-primary/30 flex items-center justify-between shadow-2xl animate-fade-in">
              <span className="text-xs text-content-primary truncate max-w-[200px]">
                Removed &ldquo;{undoItem.track.title}&rdquo;
              </span>
              <button
                onClick={handleUndoRemove}
                className="flex items-center space-x-1 text-xs font-bold text-primary hover:underline px-2 py-1"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>Undo</span>
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
