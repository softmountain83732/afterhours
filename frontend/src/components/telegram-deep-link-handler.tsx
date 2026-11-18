"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";

export function TelegramDeepLinkHandler() {
  const router = useRouter();
  const routedRef = useRef(false);

  useEffect(() => {
    if (routedRef.current) return;
    if (typeof window === "undefined") return;

    const tg = (window as any).Telegram?.WebApp;
    const startParam = tg?.initDataUnsafe?.start_param;

    if (startParam && typeof startParam === "string") {
      routedRef.current = true;
      if (startParam.startsWith("room_")) {
        const roomId = startParam.slice("room_".length).trim();
        if (roomId) {
          router.push(`/room/${roomId}`);
        }
      } else if (startParam.startsWith("track_")) {
        const trackId = startParam.slice("track_".length).trim();
        if (trackId) {
          router.push(`/library?track=${trackId}`);
        } else {
          router.push("/library");
        }
      }
    }
  }, [router]);

  return null;
}
