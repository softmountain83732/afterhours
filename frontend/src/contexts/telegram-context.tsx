"use client";

import React, { createContext, useContext, useEffect, useState } from "react";

interface TelegramUser {
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
  user: TelegramUser | null;
  haptic: (type?: "light" | "medium" | "heavy" | "rigid" | "soft") => void;
  notificationOccurred: (type: "error" | "success" | "warning") => void;
  close: () => void;
}

const TelegramContext = createContext<TelegramContextType>({
  isReady: false,
  isTelegram: false,
  initData: "",
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
  const [user, setUser] = useState<TelegramUser | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined") {
      const tg = (window as any).Telegram?.WebApp;
      if (tg && tg.initData) {
        setIsTelegram(true);
        setInitData(tg.initData);
        setUser(tg.initDataUnsafe?.user || null);

        // Tell Telegram the webapp is ready
        tg.ready();
        tg.expand();

        // Dark atmospheric theme color matching Afterhours
        tg.setHeaderColor?.("#08080c");
        tg.setBackgroundColor?.("#08080c");
      } else {
        // Normal browser mode fallback for testing
        setIsTelegram(false);
        setInitData("mock_123456_alex_Alex");
      }
      setIsReady(true);
    }
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
