"use client";

import React, { createContext, useContext, useEffect, useRef, useState } from "react";
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

  const { haptic } = useTelegram();
  const playStartTimestampRef = useRef<number>(0);

  const setPlaybackRate = (rate: number) => {
    if (audioRef.current) {
      audioRef.current.playbackRate = rate;
      setPlaybackRateState(rate);
    }
  };

  const getAudioElement = () => audioRef.current;

  // Initialize audio element
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
        setBuffered(audio.buffered.end(audio.buffered.length - 1));
      }
    };

    const onDurationChange = () => {
      if (audio.duration && !isNaN(audio.duration)) {
        setDuration(audio.duration);
      }
    };

    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);

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
  }, []);

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
  }, [currentTrack, isPlaying]);

  const handleTrackEnd = () => {
    // Record history
    if (currentTrack && audioRef.current) {
      const listenedMs = Math.round(audioRef.current.currentTime * 1000);
      if (listenedMs > 10000) {
        api.history.record(currentTrack.id, listenedMs).catch(() => {});
      }
    }

    if (repeatMode === "one" && audioRef.current) {
      audioRef.current.currentTime = 0;
      audioRef.current.play().catch(() => {});
    } else {
      next();
    }
  };

  const playTrack = (track: Track, newQueue?: Track[]) => {
    haptic("light");
    if (!audioRef.current) return;

    // Record previous track history if listened
    if (currentTrack && audioRef.current.currentTime > 10) {
      api.history.record(currentTrack.id, Math.round(audioRef.current.currentTime * 1000)).catch(() => {});
    }

    setCurrentTrack(track);
    if (newQueue) {
      setQueue(newQueue);
    } else if (!queue.some((t) => t.id === track.id)) {
      setQueue((prev) => [track, ...prev]);
    }

    const streamUrl = api.streaming.getUrl(track.id, quality);
    audioRef.current.src = streamUrl;
    audioRef.current.currentTime = 0;
    setCurrentTime(0);
    setDuration(track.duration_ms ? track.duration_ms / 1000 : 0);

    audioRef.current.play().catch(() => {
      setIsPlaying(false);
    });
  };

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

  const next = () => {
    haptic("light");
    if (queue.length === 0 || !currentTrack) return;
    const currentIndex = queue.findIndex((t) => t.id === currentTrack.id);

    if (isShuffle) {
      const remaining = queue.filter((t) => t.id !== currentTrack.id);
      if (remaining.length > 0) {
        const randomTrack = remaining[Math.floor(Math.random() * remaining.length)];
        playTrack(randomTrack);
        return;
      }
    }

    if (currentIndex >= 0 && currentIndex < queue.length - 1) {
      playTrack(queue[currentIndex + 1]);
    } else if (repeatMode === "all" && queue.length > 0) {
      playTrack(queue[0]);
    }
  };

  const previous = () => {
    haptic("light");
    if (!audioRef.current || queue.length === 0 || !currentTrack) return;
    // If more than 3 seconds in, restart track
    if (audioRef.current.currentTime > 3) {
      audioRef.current.currentTime = 0;
      setCurrentTime(0);
      return;
    }

    const currentIndex = queue.findIndex((t) => t.id === currentTrack.id);
    if (currentIndex > 0) {
      playTrack(queue[currentIndex - 1]);
    } else if (queue.length > 0) {
      playTrack(queue[queue.length - 1]);
    }
  };

  const addToQueue = (track: Track) => {
    haptic("light");
    setQueue((prev) => [...prev, track]);
  };

  const toggleShuffle = () => {
    haptic("light");
    setIsShuffle((prev) => !prev);
  };

  const toggleRepeat = () => {
    haptic("light");
    setRepeatMode((prev) => (prev === "off" ? "all" : prev === "all" ? "one" : "off"));
  };

  const setQuality = (q: AudioQuality) => {
    setQualityState(q);
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
