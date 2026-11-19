"use client";

import React, { createContext, useContext, useEffect, useRef, useState, useCallback } from "react";
import { Track, AudioQuality } from "@/lib/types";
import { api } from "@/lib/api";
import { useTelegram } from "./telegram-context";

interface PlayerContextType {
  currentTrack: Track | null;
  isPlaying: boolean;
  currentTime: number;
  duration: number;
  buffered: number;
  volume: number;
  queue: Track[];
  isShuffle: boolean;
  repeatMode: "off" | "all" | "one";
  quality: AudioQuality;
  isFullPlayerOpen: boolean;
  playbackRate: number;
  setPlaybackRate: (rate: number) => void;
  getAudioElement: () => HTMLAudioElement | null;
  playTrack: (track: Track, newQueue?: Track[]) => void;
  togglePlay: () => void;
  pause: () => void;
  resume: () => void;
  seek: (timeSeconds: number) => void;
  next: () => void;
  previous: () => void;
  addToQueue: (track: Track) => void;
  playNext: (track: Track) => void;
  removeFromQueue: (index: number) => void;
  reorderQueue: (fromIndex: number, toIndex: number) => void;
  clearQueue: () => void;
  toggleShuffle: () => void;
  toggleRepeat: () => void;
  setQuality: (quality: AudioQuality) => void;
  setVolume: (volume: number) => void;
  setIsFullPlayerOpen: (open: boolean) => void;
  toggleFavorite: (track: Track) => Promise<void>;
}

const PlayerContext = createContext<PlayerContextType>({
  currentTrack: null,
  isPlaying: false,
  currentTime: 0,
  duration: 0,
  buffered: 0,
  volume: 1,
  queue: [],
  isShuffle: false,
  repeatMode: "off",
  quality: "standard",
  isFullPlayerOpen: false,
  playbackRate: 1.0,
  setPlaybackRate: () => {},
  getAudioElement: () => null,
  playTrack: () => {},
  togglePlay: () => {},
  pause: () => {},
  resume: () => {},
  seek: () => {},
  next: () => {},
  previous: () => {},
  addToQueue: () => {},
  playNext: () => {},
  removeFromQueue: () => {},
  reorderQueue: () => {},
  clearQueue: () => {},
  toggleShuffle: () => {},
  toggleRepeat: () => {},
  setQuality: () => {},
  setVolume: () => {},
  setIsFullPlayerOpen: () => {},
  toggleFavorite: async () => {},
});

export const usePlayer = () => useContext(PlayerContext);

export const PlayerProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // React state
  const [currentTrack, setCurrentTrack] = useState<Track | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [buffered, setBuffered] = useState(0);
  const [volume, setVolumeState] = useState(1);
  const [queue, setQueue] = useState<Track[]>([]);
  const [isShuffle, setIsShuffle] = useState(false);
  const [repeatMode, setRepeatMode] = useState<"off" | "all" | "one">("off");
  const [quality, setQualityState] = useState<AudioQuality>("standard");
  const [isFullPlayerOpen, setIsFullPlayerOpen] = useState(false);
  const [playbackRate, setPlaybackRateState] = useState(1.0);

  // Synchronized refs to guarantee zero stale closures in audio event listeners
  const currentTrackRef = useRef<Track | null>(null);
  const queueRef = useRef<Track[]>([]);
  const originalQueueRef = useRef<Track[]>([]);
  const isShuffleRef = useRef<boolean>(false);
  const repeatModeRef = useRef<"off" | "all" | "one">("off");
  const qualityRef = useRef<AudioQuality>("standard");
  const isPlayingRef = useRef<boolean>(false);
  const isTransitioningRef = useRef<boolean>(false);

  const { haptic } = useTelegram();

  // Sync state to refs on every render
  useEffect(() => {
    currentTrackRef.current = currentTrack;
  }, [currentTrack]);

  useEffect(() => {
    queueRef.current = queue;
  }, [queue]);

  useEffect(() => {
    isShuffleRef.current = isShuffle;
  }, [isShuffle]);

  useEffect(() => {
    repeatModeRef.current = repeatMode;
  }, [repeatMode]);

  useEffect(() => {
    qualityRef.current = quality;
  }, [quality]);

  useEffect(() => {
    isPlayingRef.current = isPlaying;
  }, [isPlaying]);

  const setPlaybackRate = (rate: number) => {
    if (audioRef.current) {
      audioRef.current.playbackRate = rate;
      setPlaybackRateState(rate);
    }
  };

  const getAudioElement = () => audioRef.current;

  // Internal track playback executor
  const playTrackInternal = useCallback((track: Track, newQueue?: Track[]) => {
    if (!audioRef.current) return;

    // Record previous track history if played for at least 5 seconds
    const prev = currentTrackRef.current;
    if (prev && audioRef.current.currentTime > 5) {
      api.history.record(prev.id, Math.round(audioRef.current.currentTime * 1000)).catch(() => {});
    }

    setCurrentTrack(track);
    currentTrackRef.current = track;

    if (newQueue) {
      setQueue(newQueue);
      queueRef.current = newQueue;
      originalQueueRef.current = [...newQueue];
    } else if (!queueRef.current.some((t) => t.id === track.id)) {
      const updated = [track, ...queueRef.current];
      setQueue(updated);
      queueRef.current = updated;
    }

    const streamUrl = api.streaming.getUrl(track.id, qualityRef.current);
    audioRef.current.src = streamUrl;
    audioRef.current.currentTime = 0;
    setCurrentTime(0);
    setDuration(track.duration_ms ? track.duration_ms / 1000 : 0);

    audioRef.current.play().catch(() => {
      setIsPlaying(false);
      isPlayingRef.current = false;
    });
  }, []);

  const playTrack = useCallback((track: Track, newQueue?: Track[]) => {
    haptic("light");
    playTrackInternal(track, newQueue);
  }, [haptic, playTrackInternal]);

  // Robust Next Track Handler with Repeat & Shuffle Awareness
  const next = useCallback(() => {
    haptic("light");
    const currentQueue = queueRef.current;
    const cur = currentTrackRef.current;
    if (currentQueue.length === 0 || !cur) return;

    const currentIndex = currentQueue.findIndex((t) => t.id === cur.id);

    if (currentIndex >= 0 && currentIndex < currentQueue.length - 1) {
      playTrackInternal(currentQueue[currentIndex + 1]);
    } else if (repeatModeRef.current === "all" && currentQueue.length > 0) {
      playTrackInternal(currentQueue[0]);
    } else if (repeatModeRef.current === "one" && audioRef.current) {
      audioRef.current.currentTime = 0;
      audioRef.current.play().catch(() => {});
    } else {
      // End of queue in repeat "off" mode: stop cleanly
      setIsPlaying(false);
      isPlayingRef.current = false;
      if (audioRef.current) {
        audioRef.current.pause();
      }
    }
  }, [haptic, playTrackInternal]);

  // Previous Track Handler
  const previous = useCallback(() => {
    haptic("light");
    const audio = audioRef.current;
    const currentQueue = queueRef.current;
    const cur = currentTrackRef.current;
    if (!audio || currentQueue.length === 0 || !cur) return;

    // If more than 3 seconds in, restart current track
    if (audio.currentTime > 3) {
      audio.currentTime = 0;
      setCurrentTime(0);
      return;
    }

    const currentIndex = currentQueue.findIndex((t) => t.id === cur.id);
    if (currentIndex > 0) {
      playTrackInternal(currentQueue[currentIndex - 1]);
    } else if (currentQueue.length > 0) {
      playTrackInternal(currentQueue[currentQueue.length - 1]);
    }
  }, [haptic, playTrackInternal]);

  // Track End Handler (Autoplay Engine)
  const handleTrackEnd = useCallback(() => {
    if (isTransitioningRef.current) return;
    isTransitioningRef.current = true;
    setTimeout(() => {
      isTransitioningRef.current = false;
    }, 500);

    const cur = currentTrackRef.current;
    const audio = audioRef.current;
    if (cur && audio) {
      const listenedMs = Math.round(audio.currentTime * 1000);
      if (listenedMs > 5000) {
        api.history.record(cur.id, listenedMs).catch(() => {});
      }
    }

    const mode = repeatModeRef.current;
    const currentQueue = queueRef.current;

    // Repeat One: replay current track immediately
    if (mode === "one" && audio && cur) {
      audio.currentTime = 0;
      audio.play().catch(() => {});
      return;
    }

    if (!cur || currentQueue.length === 0) {
      setIsPlaying(false);
      isPlayingRef.current = false;
      return;
    }

    const currentIndex = currentQueue.findIndex((t) => t.id === cur.id);

    if (currentIndex >= 0 && currentIndex < currentQueue.length - 1) {
      // Autoplay next track in queue
      playTrackInternal(currentQueue[currentIndex + 1]);
    } else if (mode === "all" && currentQueue.length > 0) {
      // Repeat All: loop back to first track in queue
      playTrackInternal(currentQueue[0]);
    } else {
      // Repeat Off: finish playback at end of queue
      setIsPlaying(false);
      isPlayingRef.current = false;
      if (audio) {
        audio.pause();
      }
    }
  }, [playTrackInternal]);

  // Audio element setup and lifecycle
  useEffect(() => {
    const audio = new Audio();
    audio.preload = "auto";
    if ("preservesPitch" in audio) {
      (audio as any).preservesPitch = true;
    }
    audioRef.current = audio;

    const onTimeUpdate = () => {
      setCurrentTime(audio.currentTime);
      if (audio.buffered.length > 0) {
        try {
          setBuffered(audio.buffered.end(audio.buffered.length - 1));
        } catch {
          // ignore index errors
        }
      }
    };

    const onDurationChange = () => {
      if (audio.duration && !isNaN(audio.duration)) {
        setDuration(audio.duration);
      }
    };

    const onPlay = () => {
      setIsPlaying(true);
      isPlayingRef.current = true;
    };

    const onPause = () => {
      setIsPlaying(false);
      isPlayingRef.current = false;
    };

    const onEnded = () => {
      handleTrackEnd();
    };

    audio.addEventListener("timeupdate", onTimeUpdate);
    audio.addEventListener("durationchange", onDurationChange);
    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onPause);
    audio.addEventListener("ended", onEnded);

    return () => {
      audio.pause();
      audio.removeEventListener("timeupdate", onTimeUpdate);
      audio.removeEventListener("durationchange", onDurationChange);
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onPause);
      audio.removeEventListener("ended", onEnded);
    };
  }, [handleTrackEnd]);

  // Media Session API for Lock Screen & Background Playback controls
  useEffect(() => {
    if (typeof window === "undefined" || !("mediaSession" in navigator)) return;

    if (currentTrack) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: currentTrack.title,
        artist: currentTrack.artist_name || "Unknown Artist",
        album: currentTrack.album_title || "Afterhours",
        artwork: currentTrack.artwork_url
          ? [
              { src: currentTrack.artwork_url, sizes: "512x512", type: "image/jpeg" },
              { src: currentTrack.artwork_thumb_url || currentTrack.artwork_url, sizes: "128x128", type: "image/jpeg" },
            ]
          : [],
      });
    }

    navigator.mediaSession.playbackState = isPlaying ? "playing" : "paused";

    try {
      navigator.mediaSession.setActionHandler("play", () => resume());
      navigator.mediaSession.setActionHandler("pause", () => pause());
      navigator.mediaSession.setActionHandler("previoustrack", () => previous());
      navigator.mediaSession.setActionHandler("nexttrack", () => next());
      navigator.mediaSession.setActionHandler("seekto", (details) => {
        if (details.seekTime !== undefined) {
          seek(details.seekTime);
        }
      });
    } catch {}
  }, [currentTrack, isPlaying, next, previous]);

  const togglePlay = () => {
    haptic("light");
    if (!audioRef.current || !currentTrack) return;
    if (isPlaying) {
      audioRef.current.pause();
    } else {
      audioRef.current.play().catch(() => {});
    }
  };

  const pause = () => {
    audioRef.current?.pause();
  };

  const resume = () => {
    audioRef.current?.play().catch(() => {});
  };

  const seek = (timeSeconds: number) => {
    haptic("soft");
    if (!audioRef.current) return;
    audioRef.current.currentTime = timeSeconds;
    setCurrentTime(timeSeconds);
  };

  // Queue Operations
  const addToQueue = (track: Track) => {
    haptic("light");
    setQueue((prev) => {
      const updated = [...prev, track];
      queueRef.current = updated;
      return updated;
    });
    originalQueueRef.current.push(track);
  };

  const playNext = (track: Track) => {
    haptic("light");
    setQueue((prev) => {
      const cur = currentTrackRef.current;
      if (!cur) {
        queueRef.current = [track, ...prev];
        return [track, ...prev];
      }
      const curIndex = prev.findIndex((t) => t.id === cur.id);
      const updated = [...prev];
      if (curIndex >= 0) {
        updated.splice(curIndex + 1, 0, track);
      } else {
        updated.unshift(track);
      }
      queueRef.current = updated;
      return updated;
    });
  };

  const removeFromQueue = (index: number) => {
    haptic("light");
    setQueue((prev) => {
      const updated = prev.filter((_, idx) => idx !== index);
      queueRef.current = updated;
      return updated;
    });
  };

  const reorderQueue = (fromIndex: number, toIndex: number) => {
    haptic("medium");
    setQueue((prev) => {
      const updated = [...prev];
      const [moved] = updated.splice(fromIndex, 1);
      updated.splice(toIndex, 0, moved);
      queueRef.current = updated;
      return updated;
    });
  };

  const clearQueue = () => {
    haptic("medium");
    setQueue((prev) => {
      const cur = currentTrackRef.current;
      const updated = cur ? [cur] : [];
      queueRef.current = updated;
      originalQueueRef.current = [...updated];
      return updated;
    });
  };

  // True Queue Shuffling
  const toggleShuffle = () => {
    haptic("light");
    const nextShuffle = !isShuffleRef.current;
    setIsShuffle(nextShuffle);
    isShuffleRef.current = nextShuffle;

    const cur = currentTrackRef.current;
    if (nextShuffle) {
      // Preserve original queue before shuffling
      originalQueueRef.current = [...queueRef.current];
      if (cur && queueRef.current.length > 1) {
        const remaining = queueRef.current.filter((t) => t.id !== cur.id);
        // Fisher-Yates shuffle algorithm
        for (let i = remaining.length - 1; i > 0; i--) {
          const j = Math.floor(Math.random() * (i + 1));
          [remaining[i], remaining[j]] = [remaining[j], remaining[i]];
        }
        const shuffled = [cur, ...remaining];
        setQueue(shuffled);
        queueRef.current = shuffled;
      }
    } else {
      // Restore original queue order
      if (originalQueueRef.current.length > 0) {
        const restored = [...originalQueueRef.current];
        setQueue(restored);
        queueRef.current = restored;
      }
    }
  };

  // Repeat Modes: "off" -> "all" -> "one" -> "off"
  const toggleRepeat = () => {
    haptic("light");
    const nextMode: "off" | "all" | "one" =
      repeatModeRef.current === "off"
        ? "all"
        : repeatModeRef.current === "all"
        ? "one"
        : "off";
    setRepeatMode(nextMode);
    repeatModeRef.current = nextMode;
  };

  const setQuality = (q: AudioQuality) => {
    setQualityState(q);
    qualityRef.current = q;
    if (currentTrack && audioRef.current) {
      const curTime = audioRef.current.currentTime;
      const wasPlaying = !audioRef.current.paused;
      audioRef.current.src = api.streaming.getUrl(currentTrack.id, q);
      audioRef.current.currentTime = curTime;
      if (wasPlaying) {
        audioRef.current.play().catch(() => {});
      }
    }
  };

  const setVolume = (v: number) => {
    if (audioRef.current) {
      audioRef.current.volume = Math.max(0, Math.min(1, v));
    }
    setVolumeState(v);
  };

  const toggleFavorite = async (track: Track) => {
    haptic("medium");
    const wasFav = track.is_favorite;
    // Optimistic update
    track.is_favorite = !wasFav;
    if (currentTrack?.id === track.id) {
      setCurrentTrack({ ...currentTrack, is_favorite: !wasFav });
    }

    try {
      if (wasFav) {
        await api.favorites.remove(track.id);
      } else {
        await api.favorites.add(track.id);
      }
    } catch {
      // Revert on failure
      track.is_favorite = wasFav;
      if (currentTrack?.id === track.id) {
        setCurrentTrack({ ...currentTrack, is_favorite: wasFav });
      }
    }
  };

  return (
    <PlayerContext.Provider
      value={{
        currentTrack,
        isPlaying,
        currentTime,
        duration,
        buffered,
        volume,
        queue,
        isShuffle,
        repeatMode,
        quality,
        isFullPlayerOpen,
        playbackRate,
        setPlaybackRate,
        getAudioElement,
        playTrack,
        togglePlay,
        pause,
        resume,
        seek,
        next,
        previous,
        addToQueue,
        playNext,
        removeFromQueue,
        reorderQueue,
        clearQueue,
        toggleShuffle,
        toggleRepeat,
        setQuality,
        setVolume,
        setIsFullPlayerOpen,
        toggleFavorite,
      }}
    >
      {children}
    </PlayerContext.Provider>
  );
};
