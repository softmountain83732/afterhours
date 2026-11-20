"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { Room, Track, QueueItem } from "@/lib/types";
import { api } from "@/lib/api";
import { usePlayer } from "@/contexts/player-context";
import { supabase } from "@/lib/supabase";

export interface RoomSyncConfig {
  defaultLeadTimeMs?: number;
  minLeadTimeMs?: number;
  maxLeadTimeMs?: number;
  driftThresholdInSyncMs?: number;
  driftThresholdMildMs?: number;
  driftThresholdSevereMs?: number;
  rateMildSlow?: number;
  rateMildFast?: number;
  rateAggressiveSlow?: number;
  rateAggressiveFast?: number;
  rateCooldownMs?: number;
}

const DEFAULT_CONFIG: Required<RoomSyncConfig> = {
  defaultLeadTimeMs: 800,
  minLeadTimeMs: 500,
  maxLeadTimeMs: 1500,
  driftThresholdInSyncMs: 80,
  driftThresholdMildMs: 500,
  driftThresholdSevereMs: 1500,
  rateMildSlow: 0.98,
  rateMildFast: 1.02,
  rateAggressiveSlow: 0.95,
  rateAggressiveFast: 1.05,
  rateCooldownMs: 3000,
};

export function useRoomSync(roomId: string, customConfig?: RoomSyncConfig) {
  const config = { ...DEFAULT_CONFIG, ...customConfig };
  const {
    currentTrack,
    playTrack,
    pause,
    resume,
    seek,
    setPlaybackRate,
    playbackRate,
    getAudioElement,
  } = usePlayer();

  const [room, setRoom] = useState<Room | null>(null);
  const [normalizedQueue, setNormalizedQueue] = useState<QueueItem[]>([]);
  const [clockOffsetMs, setClockOffsetMs] = useState<number>(0);
  const [currentDriftMs, setCurrentDriftMs] = useState<number>(0);
  const [needsAudioUnlock, setNeedsAudioUnlock] = useState<boolean>(false);
  const [syncStatus, setSyncStatus] = useState<"calibrating" | "synced" | "adjusting" | "resyncing">("calibrating");

  // Keep references to isolate high-frequency drift & rate changes from React component re-mounts (CB-02)
  const roomRef = useRef<Room | null>(null);
  const clockOffsetRef = useRef<number>(0);
  const playbackRateRef = useRef<number>(playbackRate);
  const lastRateChangeRef = useRef<number>(0);
  const scheduledTimerRef = useRef<NodeJS.Timeout | null>(null);
  const syncLoopRef = useRef<NodeJS.Timeout | null>(null);
  const isMountedRef = useRef<boolean>(true);
  const isSubscribedRef = useRef<boolean>(false);
  const checkDriftRef = useRef<() => void>(() => {});

  useEffect(() => {
    roomRef.current = room;
  }, [room]);

  useEffect(() => {
    clockOffsetRef.current = clockOffsetMs;
  }, [clockOffsetMs]);

  useEffect(() => {
    playbackRateRef.current = playbackRate;
  }, [playbackRate]);

  // ─── 0. BUFFERED WEBKIT METADATA SEEK (MP-03) ──────────────────────────────
  const safeSeek = useCallback(
    (targetSeconds: number) => {
      const audio = getAudioElement();
      if (!audio) return;
      if (audio.readyState >= 1) {
        seek(targetSeconds);
      } else {
        const onLoadedMetadata = () => {
          audio.removeEventListener("loadedmetadata", onLoadedMetadata);
          seek(targetSeconds);
        };
        audio.addEventListener("loadedmetadata", onLoadedMetadata, { once: true });
      }
    },
    [getAudioElement, seek]
  );

  // ─── 1. NTP-STYLE CLOCK OFFSET CALIBRATION ─────────────────────────────────
  const calibrateClock = useCallback(async () => {
    const samples: number[] = [];
    for (let i = 0; i < 4; i++) {
      try {
        const t0 = Date.now();
        const res = await api.rooms.syncClock(t0);
        const t3 = Date.now();
        const rtt = t3 - t0;
        const serverTime = res.server_time_ms;
        // theta = serverTime - (t0 + RTT / 2)
        const offset = serverTime - (t0 + rtt / 2);
        samples.push(offset);
      } catch {
        // network retry
      }
    }
    if (samples.length > 0) {
      // Discard min and max outliers if >= 3 samples
      samples.sort((a, b) => a - b);
      const median = samples[Math.floor(samples.length / 2)];
      setClockOffsetMs(median);
      clockOffsetRef.current = median;
      return median;
    }
    return 0;
  }, []);

  // ─── 2. iOS / TELEGRAM WEBVIEW GESTURE UNLOCK ──────────────────────────────
  const unlockAudio = useCallback(async () => {
    const audio = getAudioElement();
    if (!audio) return;
    try {
      // Prime audio context with muted tap
      audio.muted = true;
      await audio.play();
      audio.pause();
      audio.muted = false;
      setNeedsAudioUnlock(false);
    } catch {
      // Still requires user interaction
    }
  }, [getAudioElement]);

  // Check if WebKit audio unlock is required
  useEffect(() => {
    const isIOS =
      typeof navigator !== "undefined" &&
      (/iPad|iPhone|iPod/.test(navigator.userAgent) ||
        (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1));
    if (isIOS) {
      setNeedsAudioUnlock(true);
    }
  }, []);

  // ─── 3. STATE RECONCILIATION & SCHEDULED PLAYBACK ─────────────────────────
  const applyPlaybackState = useCallback(
    (targetRoom: Room, offset: number) => {
      const audio = getAudioElement();
      if (!audio) return;

      const targetTrack = targetRoom.current_track;
      if (!targetTrack) {
        pause();
        return;
      }

      // Track switch
      if (!currentTrack || currentTrack.id !== targetTrack.id) {
        playTrack(targetTrack);
      }

      if (!targetRoom.is_playing) {
        pause();
        const staticSeconds = targetRoom.playback_position_ms / 1000;
        if (Math.abs(audio.currentTime - staticSeconds) > 0.1) {
          safeSeek(staticSeconds);
        }
        if (playbackRateRef.current !== 1.0) {
          setPlaybackRate(1.0);
          playbackRateRef.current = 1.0;
        }
        setSyncStatus("synced");
        return;
      }

      const nowServerMs = Date.now() + offset;
      const scheduledStartMs = targetRoom.scheduled_start_ms;

      if (scheduledStartMs && scheduledStartMs > nowServerMs) {
        // Coordinated scheduled future start
        const delayMs = scheduledStartMs - nowServerMs;
        const initialSeconds = targetRoom.playback_position_ms / 1000;
        safeSeek(initialSeconds);
        pause();

        if (scheduledTimerRef.current) clearTimeout(scheduledTimerRef.current);
        scheduledTimerRef.current = setTimeout(() => {
          if (!isMountedRef.current) return;
          resume();
          setSyncStatus("synced");
        }, delayMs);
      } else {
        // Ongoing playback - calculate live elapsed expected position
        let elapsedMs = 0;
        if (targetRoom.server_timestamp) {
          const stateEpoch = new Date(targetRoom.server_timestamp).getTime();
          elapsedMs = Math.max(0, nowServerMs - stateEpoch);
        }
        const expectedSeconds = (targetRoom.playback_position_ms + elapsedMs) / 1000;
        const drift = audio.currentTime - expectedSeconds;
        const driftMs = Math.abs(drift * 1000);

        if (driftMs >= config.driftThresholdSevereMs) {
          safeSeek(expectedSeconds);
          if (playbackRateRef.current !== 1.0) {
            setPlaybackRate(1.0);
            playbackRateRef.current = 1.0;
          }
        }
        resume();
      }
    },
    [currentTrack, getAudioElement, pause, playTrack, resume, safeSeek, setPlaybackRate, config]
  );

  // ─── 4. FOUR-TIER DRIFT CORRECTION ENGINE (CB-02) ─────────────────────────
  const checkDrift = useCallback(() => {
    const audio = getAudioElement();
    const activeRoom = roomRef.current;
    if (!audio || !activeRoom || !activeRoom.is_playing || needsAudioUnlock) return;

    const nowServerMs = Date.now() + clockOffsetRef.current;
    let elapsedMs = 0;
    if (activeRoom.server_timestamp) {
      const stateEpoch = new Date(activeRoom.server_timestamp).getTime();
      elapsedMs = Math.max(0, nowServerMs - stateEpoch);
    }
    const expectedSeconds = (activeRoom.playback_position_ms + elapsedMs) / 1000;
    const deltaSeconds = audio.currentTime - expectedSeconds;
    const deltaMs = deltaSeconds * 1000;
    const absDeltaMs = Math.abs(deltaMs);

    setCurrentDriftMs(Math.round(deltaMs));

    const now = Date.now();
    const isCooldownActive = now - lastRateChangeRef.current < config.rateCooldownMs;
    const currentRate = playbackRateRef.current;

    // Tier 1: In Sync (< 80ms)
    if (absDeltaMs < config.driftThresholdInSyncMs) {
      if (currentRate !== 1.0) {
        setPlaybackRate(1.0);
        playbackRateRef.current = 1.0;
        lastRateChangeRef.current = now;
      }
      setSyncStatus("synced");
      return;
    }

    // Tier 4: Severe Desync (>= 1500ms) -> Hard Seek
    if (absDeltaMs >= config.driftThresholdSevereMs) {
      safeSeek(expectedSeconds);
      if (currentRate !== 1.0) {
        setPlaybackRate(1.0);
        playbackRateRef.current = 1.0;
      }
      lastRateChangeRef.current = now;
      setSyncStatus("synced");
      return;
    }

    // Cooldown guard to avoid rate oscillation
    if (isCooldownActive) return;

    // Tier 2: Mild Drift (80ms - 500ms) -> Rate [0.98x, 1.02x]
    if (absDeltaMs < config.driftThresholdMildMs) {
      const targetRate = deltaMs < 0 ? config.rateMildFast : config.rateMildSlow;
      if (currentRate !== targetRate) {
        setPlaybackRate(targetRate);
        playbackRateRef.current = targetRate;
        lastRateChangeRef.current = now;
        setSyncStatus("adjusting");
      }
      return;
    }

    // Tier 3: Moderate Drift (500ms - 1500ms) -> Rate [0.95x, 1.05x]
    if (absDeltaMs < config.driftThresholdSevereMs) {
      const targetRate = deltaMs < 0 ? config.rateAggressiveFast : config.rateAggressiveSlow;
      if (currentRate !== targetRate) {
        setPlaybackRate(targetRate);
        playbackRateRef.current = targetRate;
        lastRateChangeRef.current = now;
        setSyncStatus("adjusting");
      }
      return;
    }
  }, [getAudioElement, needsAudioUnlock, safeSeek, setPlaybackRate, config]);

  // Keep checkDriftRef pointing to the latest callback without triggering interval recreation
  useEffect(() => {
    checkDriftRef.current = checkDrift;
  }, [checkDrift]);

  // ─── 5. DATA FETCHING & SYNCHRONIZATION ────────────────────────────────────
  const fetchNormalizedQueue = useCallback(async () => {
    try {
      const queueData = await api.rooms.getNormalizedQueue(roomId);
      if (isMountedRef.current) {
        setNormalizedQueue(queueData);
      }
    } catch {
      // ignore transient errors
    }
  }, [roomId]);

  const fetchRoomSnapshot = useCallback(async (): Promise<Room | null> => {
    try {
      const [roomData, queueData] = await Promise.all([
        api.rooms.get(roomId),
        api.rooms.getNormalizedQueue(roomId).catch(() => []),
      ]);
      if (!isMountedRef.current) return null;
      setRoom(roomData);
      roomRef.current = roomData;
      setNormalizedQueue(queueData);
      applyPlaybackState(roomData, clockOffsetRef.current);
      return roomData;
    } catch {
      // Room offline or network drop
      return null;
    }
  }, [roomId, applyPlaybackState]);

  const handleIncomingRoomState = useCallback(
    (incomingRoom: Room) => {
      if (!isMountedRef.current) return;
      // If incoming state is missing hydrated current_track, preserve current or fetch snapshot
      const current = roomRef.current;
      if (incomingRoom.current_track_id && !incomingRoom.current_track) {
        if (current?.current_track?.id === incomingRoom.current_track_id) {
          incomingRoom.current_track = current.current_track;
        } else {
          // Track changed, fetch full snapshot
          fetchRoomSnapshot();
          return;
        }
      }

      setRoom((prev) => {
        if (!prev || (incomingRoom.revision ?? 0) >= (prev.revision ?? 0)) {
          roomRef.current = incomingRoom;
          applyPlaybackState(incomingRoom, clockOffsetRef.current);
          return incomingRoom;
        }
        return prev;
      });
    },
    [applyPlaybackState, fetchRoomSnapshot]
  );

  // ─── 6. LIFECYCLE, REALTIME SUBSCRIPTIONS & BACKGROUND VISIBILITY (CB-01) ───
  useEffect(() => {
    isMountedRef.current = true;

    const init = async () => {
      const offset = await calibrateClock();
      if (!isMountedRef.current) return;
      try {
        const [roomData, queueData] = await Promise.all([
          api.rooms.get(roomId),
          api.rooms.getNormalizedQueue(roomId).catch(() => []),
        ]);
        if (!isMountedRef.current) return;
        setRoom(roomData);
        roomRef.current = roomData;
        setNormalizedQueue(queueData);
        applyPlaybackState(roomData, offset);
      } catch {}
    };

    init();

    // High frequency drift correction check (every 300ms)
    const driftInterval = setInterval(() => {
      checkDriftRef.current();
    }, 300);

    // Fallback polling interval: 2500ms when NOT subscribed to Supabase Realtime, pause when document.hidden
    syncLoopRef.current = setInterval(() => {
      if (!isSubscribedRef.current && isMountedRef.current && typeof document !== "undefined" && !document.hidden) {
        fetchRoomSnapshot();
      }
    }, 2500);

    // Realtime subscription (CB-01)
    let channel: any = null;
    if (supabase && roomId) {
      channel = supabase.channel(`room:${roomId}`);
      channel
        .on(
          "postgres_changes",
          {
            event: "*",
            schema: "public",
            table: "rooms",
            filter: `id=eq.${roomId}`,
          },
          (payload: any) => {
            if (payload.new && payload.new.id === roomId) {
              handleIncomingRoomState(payload.new as Room);
            }
          }
        )
        .on(
          "postgres_changes",
          {
            event: "*",
            schema: "public",
            table: "room_queue",
            filter: `room_id=eq.${roomId}`,
          },
          () => {
            fetchNormalizedQueue();
          }
        )
        .on("broadcast", { event: "playback_sync" }, (evt: any) => {
          if (evt.payload?.room) {
            handleIncomingRoomState(evt.payload.room);
          }
        })
        .on("broadcast", { event: "queue_updated" }, () => {
          fetchNormalizedQueue();
        })
        .on("broadcast", { event: "permissions_updated" }, () => {
          fetchRoomSnapshot();
        })
        .on("broadcast", { event: "host_transferred" }, () => {
          fetchRoomSnapshot();
        })
        .on("broadcast", { event: "member_kicked" }, () => {
          fetchRoomSnapshot();
        })
        .on("broadcast", { event: "room_closed" }, () => {
          fetchRoomSnapshot();
        })
        .subscribe((status: string) => {
          isSubscribedRef.current = status === "SUBSCRIBED";
        });
    }

    // Re-sync on visibility change (device unlock / tab return)
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        setSyncStatus("resyncing");
        calibrateClock().then((offset) => {
          if (!isMountedRef.current) return;
          api.rooms.get(roomId).then((freshRoom) => {
            if (!isMountedRef.current) return;
            setRoom(freshRoom);
            roomRef.current = freshRoom;
            applyPlaybackState(freshRoom, offset);
          });
          fetchNormalizedQueue();
        });
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      isMountedRef.current = false;
      clearInterval(driftInterval);
      if (syncLoopRef.current) clearInterval(syncLoopRef.current);
      if (scheduledTimerRef.current) clearTimeout(scheduledTimerRef.current);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      if (channel && supabase) {
        supabase.removeChannel(channel);
      }
      isSubscribedRef.current = false;
      setPlaybackRate(1.0);
    };
  }, [
    roomId,
    calibrateClock,
    fetchRoomSnapshot,
    fetchNormalizedQueue,
    handleIncomingRoomState,
    applyPlaybackState,
    setPlaybackRate,
  ]);

  // ─── 7. DISPATCH AUTHORITATIVE COMMAND WITH OPTIMISTIC UPDATES & 409 RETRY ──
  const sendCommand = useCallback(
    async (action: string, payload: any = {}, retryCount = 0): Promise<any> => {
      const activeRoom = roomRef.current || room;
      if (!activeRoom) return;

      const previousRoom = activeRoom;
      const expectedRev = activeRoom.revision;

      // Optimistic updates for responsive host controls
      if (action === "PAUSE") {
        const optimistic = { ...activeRoom, is_playing: false };
        setRoom(optimistic);
        roomRef.current = optimistic;
        pause();
      } else if (action === "PLAY") {
        const pos = payload.position_ms !== undefined ? payload.position_ms : activeRoom.playback_position_ms;
        const optimistic = {
          ...activeRoom,
          is_playing: true,
          playback_position_ms: pos,
          ...(payload.track_id ? { current_track_id: payload.track_id } : {}),
        };
        setRoom(optimistic);
        roomRef.current = optimistic;
        resume();
      } else if (action === "SEEK" && payload.position_ms !== undefined) {
        const optimistic = { ...activeRoom, playback_position_ms: payload.position_ms };
        setRoom(optimistic);
        roomRef.current = optimistic;
        safeSeek(payload.position_ms / 1000);
      } else if (action === "CHANGE_TRACK" && payload.track_id) {
        const optimistic = {
          ...activeRoom,
          current_track_id: payload.track_id,
          is_playing: true,
          playback_position_ms: 0,
        };
        setRoom(optimistic);
        roomRef.current = optimistic;
      }

      try {
        const res = await api.rooms.dispatchCommand(roomId, action, payload, expectedRev);
        if (res.success && res.room) {
          setRoom(res.room);
          roomRef.current = res.room;
          applyPlaybackState(res.room, clockOffsetRef.current);
        }
        // Refresh queue on queue-related commands
        if (action.startsWith("QUEUE_") || action === "NEXT_TRACK" || action === "CHANGE_TRACK") {
          fetchNormalizedQueue();
        }
        return res;
      } catch (err: any) {
        // HTTP 409: Revision Conflict - refresh authoritative state and retry once
        if (err?.status === 409 && retryCount < 1) {
          const freshRoom = await fetchRoomSnapshot();
          if (freshRoom) {
            return await sendCommand(action, payload, retryCount + 1);
          }
        }

        // Roll back optimistic state on error
        if (previousRoom) {
          setRoom(previousRoom);
          roomRef.current = previousRoom;
          applyPlaybackState(previousRoom, clockOffsetRef.current);
        }
        throw err;
      }
    },
    [room, roomId, pause, resume, safeSeek, applyPlaybackState, fetchNormalizedQueue, fetchRoomSnapshot]
  );

  return {
    room,
    normalizedQueue,
    clockOffsetMs,
    currentDriftMs,
    syncStatus,
    needsAudioUnlock,
    unlockAudio,
    sendCommand,
    refreshState: fetchRoomSnapshot,
  };
}
