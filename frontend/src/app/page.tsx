"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/auth-context";

export default function Home() {
  const router = useRouter();
  const { user, isLoading } = useAuth();

  useEffect(() => {
    if (!isLoading) {
      if (user && user.profile_completed === false) {
        router.replace("/setup");
      } else {
        router.replace("/library");
      }
    }
  }, [user, isLoading, router]);

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
