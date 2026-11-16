"use client";

import React from "react";
import Link from "next/link";
import { Music, Lock, Globe } from "lucide-react";
import { Playlist } from "@/lib/types";
import { useTelegram } from "@/contexts/telegram-context";

interface PlaylistCardProps {
  playlist: Playlist;
}

export const PlaylistCard: React.FC<PlaylistCardProps> = ({ playlist }) => {
  const { haptic } = useTelegram();

  const artwork = playlist.preview_artwork || "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=300&q=80";

  return (
    <Link
      href={`/playlist/${playlist.id}`}
      onClick={() => haptic("light")}
      className="flex flex-col group active:scale-[0.98] transition-transform"
    >
      <div className="relative aspect-square w-full rounded-2xl overflow-hidden bg-surface-light border border-border/80 shadow-md">
        <img
          src={artwork}
          alt={playlist.name}
          className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
        />
        <div className="absolute top-2 right-2 p-1.5 rounded-full bg-black/60 backdrop-blur-md">
          {playlist.is_public ? (
            <Globe className="w-3.5 h-3.5 text-accent" />
          ) : (
            <Lock className="w-3.5 h-3.5 text-content-muted" />
          )}
        </div>
      </div>

      <div className="flex flex-col mt-2 px-0.5">
        <span className="text-sm font-bold text-content-primary truncate group-hover:text-primary transition-colors">
          {playlist.name}
        </span>
        <span className="text-xs text-content-secondary mt-0.5">
          {playlist.track_count ?? 0} {playlist.track_count === 1 ? "track" : "tracks"}
        </span>
      </div>
    </Link>
  );
};
