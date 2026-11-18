"use client";

import React from "react";
import { TelegramProvider } from "@/contexts/telegram-context";
import { AuthProvider } from "@/contexts/auth-context";
import { PlayerProvider } from "@/contexts/player-context";
import { TelegramDeepLinkHandler } from "@/components/telegram-deep-link-handler";

export const Providers: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  return (
    <TelegramProvider>
      <AuthProvider>
        <PlayerProvider>
          <TelegramDeepLinkHandler />
          {children}
        </PlayerProvider>
      </AuthProvider>
    </TelegramProvider>
  );
};
