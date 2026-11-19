"use client";

import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { UserProfile } from "@/lib/types";
import { api, setApiToken, getApiToken } from "@/lib/api";
import { useTelegram } from "./telegram-context";

interface AuthContextType {
  user: UserProfile | null;
  isLoading: boolean;
  isGuest: boolean;
  authError: string | null;
  loginWithTelegram: (initData?: string) => Promise<UserProfile>;
  loginWithDev: () => Promise<UserProfile>;
  loginAsGuest: (displayName?: string) => Promise<UserProfile>;
  continueAsGuest: () => Promise<UserProfile>;
  retryAuth: () => Promise<void>;
  updateProfile: (data: Partial<UserProfile>) => Promise<UserProfile>;
  logout: () => void;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  isLoading: true,
  isGuest: false,
  authError: null,
  loginWithTelegram: async () => ({} as UserProfile),
  loginWithDev: async () => ({} as UserProfile),
  loginAsGuest: async () => ({} as UserProfile),
  continueAsGuest: async () => ({} as UserProfile),
  retryAuth: async () => {},
  updateProfile: async () => ({} as UserProfile),
  logout: () => {},
  refreshUser: async () => {},
});

export const useAuth = () => useContext(AuthContext);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const { isReady, isTelegram, initData } = useTelegram();

  const refreshUser = async () => {
    try {
      const profile = await api.profiles.getMe();
      setUser(profile);
      setAuthError(null);
    } catch {
      setUser(null);
      setApiToken(null);
      throw new Error("Session expired");
    }
  };

  const loginWithDev = async () => {
    setIsLoading(true);
    setAuthError(null);
    try {
      const response = await api.auth.dev();
      setApiToken(response.access_token);
      setUser(response.profile);
      return response.profile;
    } catch (err: any) {
      setAuthError(err.message || "Dev authentication failed");
      throw err;
    } finally {
      setIsLoading(false);
    }
  };

  const loginWithTelegram = async (customInitData?: string) => {
    setIsLoading(true);
    setAuthError(null);
    try {
      const dataToUse = customInitData || initData;
      if (!dataToUse) {
        throw new Error("No Telegram session data available");
      }
      const response = await api.auth.telegram(dataToUse);
      setApiToken(response.access_token);

      const profile = {
        ...response.profile,
        profile_completed: response.profile.profile_completed !== undefined
          ? response.profile.profile_completed
          : !response.is_new_user,
      };

      setUser(profile);
      return profile;
    } catch (err: any) {
      const msg = err.message || "Failed to authenticate with Telegram";
      setAuthError(msg);
      throw err;
    } finally {
      setIsLoading(false);
    }
  };

  const loginAsGuest = async (displayName?: string) => {
    setIsLoading(true);
    setAuthError(null);
    try {
      const response = await api.auth.guest(displayName);
      setApiToken(response.access_token);
      setUser(response.profile);
      return response.profile;
    } catch (err: any) {
      setAuthError(err.message || "Guest session creation failed");
      throw err;
    } finally {
      setIsLoading(false);
    }
  };

  const continueAsGuest = async () => {
    setAuthError(null);
    return loginAsGuest("Guest Listener");
  };

  const retryAuth = useCallback(async () => {
    setAuthError(null);
    if (initData) {
      try {
        await loginWithTelegram(initData);
      } catch {
        // authError is set inside loginWithTelegram
      }
    } else {
      await continueAsGuest();
    }
  }, [initData]);

  const updateProfile = async (data: Partial<UserProfile>) => {
    const updated = await api.profiles.update(data);
    setUser(updated);
    return updated;
  };

  const logout = () => {
    setApiToken(null);
    setUser(null);
    setAuthError(null);
  };

  useEffect(() => {
    if (!isReady) return;

    const existingToken = getApiToken();

    if (existingToken) {
      // Validate existing saved token
      refreshUser()
        .catch(() => {
          // Token expired or invalid; try Telegram or Guest
          if (initData) {
            loginWithTelegram(initData).catch(() => {});
          } else {
            loginAsGuest().catch(() => {});
          }
        })
        .finally(() => setIsLoading(false));
      return;
    }

    if (initData) {
      // Real Telegram WebApp initData is present: authenticate
      loginWithTelegram(initData).catch((err) => {
        // Do not silently disguise failed Telegram authentication as guest
        setAuthError(err.message || "Telegram authentication failed. Please reopen through the Telegram bot.");
      });
      return;
    }

    // Outside Telegram WebApp (e.g. desktop browser, preview)
    const isDev = typeof window !== "undefined" && (
      window.location.hostname === "localhost" ||
      window.location.hostname === "127.0.0.1"
    );

    if (isDev) {
      loginWithDev().catch(() => {
        loginAsGuest().catch(() => {});
      });
    } else {
      // Production browser outside Telegram: automatically initiate explicit Guest session
      loginAsGuest("Guest Listener").catch(() => {});
    }
  }, [isReady, initData]);

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        isGuest: !!user?.is_guest,
        authError,
        loginWithTelegram,
        loginWithDev,
        loginAsGuest,
        continueAsGuest,
        retryAuth,
        updateProfile,
        logout,
        refreshUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};
