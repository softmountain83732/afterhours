"use client";

import React from "react";
import Link from "next/link";
import { Users, Radio, Disc3 } from "lucide-react";
import { Room } from "@/lib/types";
import { useTelegram } from "@/contexts/telegram-context";

interface RoomCardProps {
  room: Room;
}

export const RoomCard: React.FC<RoomCardProps> = ({ room }) => {
  const { haptic } = useTelegram();

  return (
    <Link
      href={`/room/${room.id}`}
      onClick={() => haptic("light")}
      className="block p-4 rounded-2xl bg-surface/80 hover:bg-surface-hover border border-border/80 transition-all active:scale-[0.99] group shadow-lg"
    >
      <div className="flex items-start justify-between">
        <div className="flex flex-col min-w-0 pr-2">
          <div className="flex items-center space-x-2">
            <h3 className="text-base font-bold text-content-primary group-hover:text-primary transition-colors truncate">
              {room.name}
            </h3>
            {room.is_playing && (
              <span className="flex items-center space-x-1 px-1.5 py-0.5 rounded-full bg-primary/20 text-primary text-[10px] font-bold uppercase tracking-wider">
                <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                <span>Live</span>
              </span>
            )}
          </div>
          <span className="text-xs text-content-secondary mt-0.5">
            Host: <span className="text-content-primary font-medium">{room.host_name}</span>
          </span>
        </div>

        <div className="flex items-center space-x-1 px-2.5 py-1 rounded-full bg-white/5 border border-white/5 text-content-secondary text-xs font-medium">
          <Users className="w-3.5 h-3.5 text-accent" />
          <span>{room.listener_count}</span>
        </div>
      </div>

      {/* Now Playing preview */}
      {room.current_track && (
        <div className="mt-3.5 pt-3 border-t border-border/40 flex items-center space-x-3">
          <div className="w-8 h-8 rounded-lg overflow-hidden bg-surface-light border border-border/40 flex-shrink-0">
            <img
              src={room.current_track.artwork_thumb_url || room.current_track.artwork_url || "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=100&q=80"}
              alt=""
              className="w-full h-full object-cover"
            />
          </div>
          <div className="flex flex-col min-w-0">
            <span className="text-xs font-semibold text-content-primary truncate">
              {room.current_track.title}
            </span>
            <span className="text-[11px] text-content-muted truncate">
              {room.current_track.artist_name}
            </span>
          </div>
        </div>
      )}
    </Link>
  );
};
