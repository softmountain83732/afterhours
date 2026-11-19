"use client";

import React, { createContext, useContext, useEffect, useState } from "react";

export interface TelegramUser {
  id: number;
  first_name: string;
  last_name?: string;
  username?: string;
  language_code?: string;
  photo_url?: string;
}

interface TelegramContextType {
  isReady: boolean;
  isTelegram: boolean;
  initData: string;
  startParam: string | null;
  user: TelegramUser | null;
  haptic: (type?: "light" | "medium" | "heavy" | "rigid" | "soft") => void;
  notificationOccurred: (type: "error" | "success" | "warning") => void;
  close: () => void;
}

const TelegramContext = createContext<TelegramContextType>({
  isReady: false,
  isTelegram: false,
  initData: "",
  startParam: null,
  user: null,
  haptic: () => {},
  notificationOccurred: () => {},
  close: () => {},
});

export const useTelegram = () => useContext(TelegramContext);

export const TelegramProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [isReady, setIsReady] = useState(false);
  const [isTelegram, setIsTelegram] = useState(false);
  const [initData, setInitData] = useState<string>("");
  const [startParam, setStartParam] = useState<string | null>(null);
  const [user, setUser] = useState<TelegramUser | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;

    let attempts = 0;
    const maxAttempts = 30; // 30 * 50ms = 1500ms max detection window

    const extractTelegramData = (): {
      initData: string;
      user: TelegramUser | null;
      startParam: string | null;
      tg: any;
    } | null => {
      const tg = (window as any).Telegram?.WebApp;

      // 1. Check official Telegram WebApp object
      if (tg && typeof tg.initData === "string" && tg.initData.trim().length > 0) {
        return {
          initData: tg.initData,
          user: tg.initDataUnsafe?.user || null,
          startParam: tg.initDataUnsafe?.start_param || null,
          tg,
        };
      }

      // 2. Check URL hash for Telegram Web: #tgWebAppData=...
      if (window.location.hash && window.location.hash.includes("tgWebAppData=")) {
        try {
          const hashStr = window.location.hash.substring(1);
          const hashParams = new URLSearchParams(hashStr);
          const tgWebAppData = hashParams.get("tgWebAppData");
          if (tgWebAppData) {
            const innerParams = new URLSearchParams(tgWebAppData);
            const userStr = innerParams.get("user");
            const parsedUser = userStr ? JSON.parse(decodeURIComponent(userStr)) : null;
            const sp = innerParams.get("start_param");
            return {
              initData: tgWebAppData,
              user: parsedUser,
              startParam: sp || null,
              tg,
            };
          }
        } catch {
          // ignore parsing error
        }
      }

      // 3. Check search query for ?tgWebAppData=...
      const searchParams = new URLSearchParams(window.location.search);
      const queryInitData = searchParams.get("tgWebAppData");
      if (queryInitData) {
        try {
          const innerParams = new URLSearchParams(queryInitData);
          const userStr = innerParams.get("user");
          const parsedUser = userStr ? JSON.parse(decodeURIComponent(userStr)) : null;
          const sp = innerParams.get("start_param");
          return {
            initData: queryInitData,
            user: parsedUser,
            startParam: sp || null,
            tg,
          };
        } catch {
          return {
            initData: queryInitData,
            user: null,
            startParam: null,
            tg,
          };
        }
      }

      return null;
    };

    const interval = setInterval(() => {
      attempts++;
      const detected = extractTelegramData();

      if (detected) {
        clearInterval(interval);
        setIsTelegram(true);
        setInitData(detected.initData);
        setStartParam(detected.startParam);
        setUser(detected.user);

        if (detected.tg) {
          try {
            detected.tg.ready?.();
            detected.tg.expand?.();
            detected.tg.setHeaderColor?.("#08080c");
            detected.tg.setBackgroundColor?.("#08080c");
          } catch {
            // ignore
          }
        }
        setIsReady(true);
      } else if (attempts >= maxAttempts) {
        clearInterval(interval);
        // Not inside Telegram or initData was not provided
        setIsTelegram(false);
        setInitData("");
        setStartParam(null);
        setUser(null);
        setIsReady(true);
      }
    }, 50);

    return () => clearInterval(interval);
  }, []);

  const haptic = (type: "light" | "medium" | "heavy" | "rigid" | "soft" = "light") => {
    try {
      const tg = (window as any).Telegram?.WebApp;
      tg?.HapticFeedback?.impactOccurred(type);
    } catch {
      // ignore
    }
  };

  const notificationOccurred = (type: "error" | "success" | "warning") => {
    try {
      const tg = (window as any).Telegram?.WebApp;
      tg?.HapticFeedback?.notificationOccurred(type);
    } catch {
      // ignore
    }
  };

  const close = () => {
    try {
      (window as any).Telegram?.WebApp?.close();
    } catch {
      // ignore
    }
  };

  return (
    <TelegramContext.Provider
      value={{
        isReady,
        isTelegram,
        initData,
        startParam,
        user,
        haptic,
        notificationOccurred,
        close,
      }}
    >
      {children}
    </TelegramContext.Provider>
  );
};
