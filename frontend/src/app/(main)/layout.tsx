"use client";

import React from "react";
import { BottomNav } from "@/components/navigation/bottom-nav";
import { MiniPlayer } from "@/components/player/mini-player";
import { FullPlayer } from "@/components/player/full-player";

export default function MainLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative min-h-screen bg-background flex flex-col pb-[calc(76px+max(env(safe-area-inset-bottom),8px))]">
      <main className="flex-1 max-w-md mx-auto w-full px-4 pt-4">
        {children}
      </main>

      {/* Mini Player */}
      <MiniPlayer />

      {/* Full-Screen Immersive Player */}
      <FullPlayer />

      {/* Bottom Navigation */}
      <BottomNav />
    </div>
  );
}
