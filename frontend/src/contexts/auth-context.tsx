"use client";

import React, { createContext, useContext, useEffect, useState } from "react";
import { UserProfile } from "@/lib/types";
import { api, setApiToken, getApiToken } from "@/lib/api";
import { useTelegram } from "./telegram-context";

interface AuthContextType {
  user: UserProfile | null;
  isLoading: boolean;
  isGuest: boolean;
  loginWithTelegram: (initData?: string) => Promise<UserProfile>;
  loginWithDev: () => Promise<UserProfile>;
  loginAsGuest: (displayName?: string) => Promise<UserProfile>;
  updateProfile: (data: Partial<UserProfile>) => Promise<UserProfile>;
  logout: () => void;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  isLoading: true,
  isGuest: false,
  loginWithTelegram: async () => ({} as UserProfile),
  loginWithDev: async () => ({} as UserProfile),
  loginAsGuest: async () => ({} as UserProfile),
  updateProfile: async () => ({} as UserProfile),
  logout: () => {},
  refreshUser: async () => {},
});

export const useAuth = () => useContext(AuthContext);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const { isReady, initData } = useTelegram();

  const refreshUser = async () => {
    try {
      const profile = await api.profiles.getMe();
      setUser(profile);
    } catch {
      setUser(null);
      setApiToken(null);
    }
  };

  const loginWithDev = async () => {
    setIsLoading(true);
    try {
      const response = await api.auth.dev();
      setApiToken(response.access_token);
      setUser(response.profile);
      return response.profile;
    } finally {
      setIsLoading(false);
    }
  };

  const loginWithTelegram = async (customInitData?: string) => {
    setIsLoading(true);
    try {
      const dataToUse = customInitData || initData;
      if (!dataToUse) {
        return loginWithDev();
      }
      const response = await api.auth.telegram(dataToUse);
      setApiToken(response.access_token);
      setUser(response.profile);
      return response.profile;
    } finally {
      setIsLoading(false);
    }
  };

  const loginAsGuest = async (displayName?: string) => {
    setIsLoading(true);
    try {
      const response = await api.auth.guest(displayName);
      setApiToken(response.access_token);
      setUser(response.profile);
      return response.profile;
    } finally {
      setIsLoading(false);
    }
  };

  const updateProfile = async (data: Partial<UserProfile>) => {
    const updated = await api.profiles.update(data);
    setUser(updated);
    return updated;
  };

  const logout = () => {
    setApiToken(null);
    setUser(null);
  };

  useEffect(() => {
    if (!isReady) return;

    const existingToken = getApiToken();
    if (existingToken) {
      refreshUser().finally(() => setIsLoading(false));
    } else if (initData) {
      loginWithTelegram(initData).catch(() => {
        loginAsGuest().catch(() => {});
      });
    } else {
      // Outside Telegram WebApp (e.g., desktop dev browser)
      loginWithDev().catch(() => {
        loginAsGuest().catch(() => {});
      });
    }
  }, [isReady]);

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        isGuest: !!user?.is_guest,
        loginWithTelegram,
        loginWithDev,
        loginAsGuest,
        updateProfile,
        logout,
        refreshUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};
