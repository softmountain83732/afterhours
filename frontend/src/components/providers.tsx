"use client";

import React from "react";
import { TelegramProvider } from "@/contexts/telegram-context";
import { AuthProvider } from "@/contexts/auth-context";
import { PlayerProvider } from "@/contexts/player-context";

export const Providers: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  return (
    <TelegramProvider>
      <AuthProvider>
        <PlayerProvider>{children}</PlayerProvider>
      </AuthProvider>
    </TelegramProvider>
  );
};
