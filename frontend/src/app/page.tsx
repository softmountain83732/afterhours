"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/auth-context";
import { AlertCircle, RefreshCw, UserCheck } from "lucide-react";

export default function Home() {
  const router = useRouter();
  const { user, isLoading, authError, retryAuth, continueAsGuest } = useAuth();

  useEffect(() => {
    if (!isLoading && !authError) {
      if (user && user.profile_completed === false) {
        router.replace("/setup");
      } else if (user) {
        router.replace("/library");
      }
    }
  }, [user, isLoading, authError, router]);

  if (authError) {
    return (
      <div className="min-h-screen bg-background flex flex-col items-center justify-center p-6 text-content-primary">
        <div className="w-full max-w-sm flex flex-col items-center space-y-5 bg-surface/90 border border-border/80 rounded-3xl p-6 shadow-2xl text-center">
          <div className="w-12 h-12 rounded-2xl bg-red-500/10 border border-red-500/20 flex items-center justify-center text-red-400">
            <AlertCircle className="w-6 h-6" />
          </div>
          <div className="flex flex-col space-y-1">
            <h1 className="text-xl font-bold tracking-tight">Authentication Notice</h1>
            <p className="text-xs text-content-secondary leading-relaxed">
              {authError}
            </p>
          </div>

          <div className="w-full flex flex-col space-y-2.5 pt-2">
            <button
              onClick={() => retryAuth()}
              className="w-full flex items-center justify-center space-x-2 py-2.5 rounded-xl bg-primary hover:bg-primary-hover text-white text-xs font-semibold shadow-lg shadow-primary/20 transition-all active:scale-95"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              <span>Retry Telegram Login</span>
            </button>

            <button
              onClick={async () => {
                await continueAsGuest();
                router.replace("/library");
              }}
              className="w-full flex items-center justify-center space-x-2 py-2.5 rounded-xl bg-surface-hover hover:bg-surface-light border border-border text-content-secondary hover:text-content-primary text-xs font-semibold transition-all active:scale-95"
            >
              <UserCheck className="w-3.5 h-3.5" />
              <span>Continue as Guest</span>
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background flex flex-col items-center justify-center p-4">
      <div className="flex flex-col items-center space-y-3 animate-pulse">
        <div className="w-12 h-12 rounded-2xl bg-primary/20 border border-primary/40 flex items-center justify-center">
          <span className="text-xl font-black text-primary">A</span>
        </div>
        <span className="text-xs font-bold uppercase tracking-widest text-content-secondary">
          Afterhours
        </span>
      </div>
    </div>
  );
}
