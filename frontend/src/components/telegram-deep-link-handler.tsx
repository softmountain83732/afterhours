"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useTelegram } from "@/contexts/telegram-context";

export function TelegramDeepLinkHandler() {
  const router = useRouter();
  const { startParam: contextStartParam } = useTelegram();
  const routedRef = useRef(false);

  useEffect(() => {
    if (routedRef.current) return;
    if (typeof window === "undefined") return;

    const tg = (window as any).Telegram?.WebApp;
    // Check context param, Telegram WebApp unsafe param, and search query params
    let startParam = contextStartParam || tg?.initDataUnsafe?.start_param;
    if (!startParam) {
      try {
        const sp = new URLSearchParams(window.location.search).get("tgWebAppStartParam");
        if (sp) startParam = sp;
      } catch {}
    }

    if (startParam && typeof startParam === "string") {
      routedRef.current = true;
      const cleanParam = startParam.trim();
      if (cleanParam.startsWith("room_")) {
        const roomId = cleanParam.slice("room_".length).trim();
        if (roomId) {
          router.push(`/room/${roomId}`);
        }
      } else if (cleanParam.startsWith("track_")) {
        const trackId = cleanParam.slice("track_".length).trim();
        if (trackId) {
          router.push(`/library?track=${trackId}`);
        } else {
          router.push("/library");
        }
      }
    }
  }, [router, contextStartParam]);

  return null;
}
