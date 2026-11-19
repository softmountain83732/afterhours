"use client";

import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { UserProfile } from "@/lib/types";
import { api, setApiToken, getApiToken } from "@/lib/api";
import { useTelegram } from "./telegram-context";

export type AuthState =
  | "initializing"
  | "authenticated"
  | "requires_setup"
  | "guest"
  | "authentication_error";

interface AuthContextType {
  user: UserProfile | null;
  authState: AuthState;
  isLoading: boolean;
  isInitializing: boolean;
  isAuthenticated: boolean;
  isGuest: boolean;
  requiresSetup: boolean;
  authError: string | null;
  loginWithTelegram: (initData?: string) => Promise<UserProfile>;
  loginWithDev: () => Promise<UserProfile>;
  loginAsGuest: (displayName?: string) => Promise<UserProfile>;
  continueAsGuest: () => Promise<UserProfile>;
  retryAuth: () => Promise<void>;
  updateProfile: (data: Partial<UserProfile>) => Promise<UserProfile>;
  logout: () => void;
  refreshUser: () => Promise<UserProfile | null>;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  authState: "initializing",
  isLoading: true,
  isInitializing: true,
  isAuthenticated: false,
  isGuest: false,
  requiresSetup: false,
  authError: null,
  loginWithTelegram: async () => ({} as UserProfile),
  loginWithDev: async () => ({} as UserProfile),
  loginAsGuest: async () => ({} as UserProfile),
  continueAsGuest: async () => ({} as UserProfile),
  retryAuth: async () => {},
  updateProfile: async () => ({} as UserProfile),
  logout: () => {},
  refreshUser: async () => null,
});

export const useAuth = () => useContext(AuthContext);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [authState, setAuthState] = useState<AuthState>("initializing");
  const [isLoading, setIsLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const { isReady, initData } = useTelegram();

  const refreshUser = async (): Promise<UserProfile | null> => {
    try {
      const profile = await api.profiles.getMe();
      setUser(profile);
      setAuthError(null);

      if (profile.is_guest) {
        setAuthState("guest");
      } else if (profile.profile_completed === false) {
        setAuthState("requires_setup");
      } else {
        setAuthState("authenticated");
      }
      return profile;
    } catch {
      setUser(null);
      setApiToken(null);
      return null;
    }
  };

  const loginWithDev = async () => {
    setIsLoading(true);
    setAuthError(null);
    try {
      const response = await api.auth.dev();
      setApiToken(response.access_token);
      setUser(response.profile);
      setAuthState(response.profile.is_guest ? "guest" : "authenticated");
      return response.profile;
    } catch (err: any) {
      setAuthError(err.message || "Dev authentication failed");
      setAuthState("authentication_error");
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

      const isCompleted =
        response.profile.profile_completed !== undefined
          ? response.profile.profile_completed
          : !response.is_new_user;

      const profile: UserProfile = {
        ...response.profile,
        profile_completed: isCompleted,
      };

      setUser(profile);

      if (!isCompleted) {
        setAuthState("requires_setup");
      } else {
        setAuthState("authenticated");
      }

      return profile;
    } catch (err: any) {
      const msg = err.message || "Failed to authenticate with Telegram";
      setAuthError(msg);
      setAuthState("authentication_error");
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
      setAuthState("guest");
      return response.profile;
    } catch (err: any) {
      setAuthError(err.message || "Guest session creation failed");
      setAuthState("authentication_error");
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
    if (updated.profile_completed !== false && authState === "requires_setup") {
      setAuthState("authenticated");
    }
    return updated;
  };

  const logout = () => {
    setApiToken(null);
    setUser(null);
    setAuthError(null);
    setAuthState("initializing");
  };

  useEffect(() => {
    if (!isReady) return;

    let isCancelled = false;

    const initializeAuth = async () => {
      const existingToken = getApiToken();

      if (existingToken) {
        const validatedProfile = await refreshUser();
        if (isCancelled) return;

        if (validatedProfile) {
          setIsLoading(false);
          return;
        }
        // Existing token was invalid or expired: clear it
        setApiToken(null);
      }

      // Check for real Telegram initData
      if (initData) {
        try {
          await loginWithTelegram(initData);
        } catch {
          // authError and authState='authentication_error' already set
        }
        if (!isCancelled) setIsLoading(false);
        return;
      }

      // Outside Telegram WebApp (e.g. desktop browser, development preview)
      const isDev =
        typeof window !== "undefined" &&
        (window.location.hostname === "localhost" ||
          window.location.hostname === "127.0.0.1");

      if (isDev) {
        try {
          await loginWithDev();
        } catch {
          await loginAsGuest("Local Guest");
        }
      } else {
        // Production browser outside Telegram: explicit Guest session
        await loginAsGuest("Guest Listener");
      }

      if (!isCancelled) setIsLoading(false);
    };

    initializeAuth();

    return () => {
      isCancelled = true;
    };
  }, [isReady, initData]);

  const isInitializing = authState === "initializing";
  const isAuthenticated = authState === "authenticated";
  const isGuest = authState === "guest" || Boolean(user?.is_guest);
  const requiresSetup = authState === "requires_setup" || (user !== null && user.profile_completed === false);

  return (
    <AuthContext.Provider
      value={{
        user,
        authState,
        isLoading,
        isInitializing,
        isAuthenticated,
        isGuest,
        requiresSetup,
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
