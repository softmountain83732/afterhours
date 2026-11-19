"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, Radio, Search, X, Lock } from "lucide-react";
import { RoomCard } from "@/components/ui/room-card";
import { Room } from "@/lib/types";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/auth-context";
import { useTelegram } from "@/contexts/telegram-context";

export default function RoomsPage() {
  const router = useRouter();
  const { user, isGuest } = useAuth();
  const [rooms, setRooms] = useState<Room[]>([]);
  const [query, setQuery] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [newRoomName, setNewRoomName] = useState("");
  const [isPublic, setIsPublic] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [createError, setCreateError] = useState("");

  const { haptic } = useTelegram();

  const loadRooms = async (search = "") => {
    try {
      setIsLoading(true);
      const res = await api.rooms.list(search);
      setRooms(res);
    } catch {
      // ignore
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadRooms();
  }, []);

  const handleCreateRoom = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newRoomName.trim() || isSubmitting) return;

    if (isGuest) {
      setCreateError("Guest listeners cannot create rooms. Please log in with Telegram.");
      return;
    }

    haptic("medium");
    setIsSubmitting(true);
    setCreateError("");
    try {
      const room = await api.rooms.create({
        name: newRoomName.trim(),
        is_public: isPublic,
      });
      setIsCreateModalOpen(false);
      setNewRoomName("");
      loadRooms();
      router.push(`/room/${room.id}`);
    } catch (err: any) {
      setCreateError(err.message || "Failed to create room");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="flex flex-col space-y-5">
      {/* Header */}
      <header className="flex items-center justify-between pt-2">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-content-primary">
            Global Rooms
          </h1>
          <p className="text-xs text-content-secondary mt-0.5">
            Listen together in real-time synchronization.
          </p>
        </div>

        <button
          onClick={() => {
            haptic("light");
            setIsCreateModalOpen(true);
          }}
          className="flex items-center space-x-1.5 px-3.5 py-1.5 rounded-full bg-primary hover:bg-primary-hover text-white text-xs font-semibold shadow-lg shadow-primary/25 transition-all active:scale-95"
        >
          <Plus className="w-4 h-4" />
          <span>Create Room</span>
        </button>
      </header>

      {/* Room Search */}
      <div className="relative flex items-center w-full">
        <Search className="absolute left-3.5 w-4 h-4 text-content-muted pointer-events-none" />
        <input
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            loadRooms(e.target.value);
          }}
          placeholder="Search rooms or hosts..."
          className="w-full pl-10 pr-4 py-2.5 rounded-xl bg-surface-hover/80 border border-border/80 text-sm text-content-primary placeholder:text-content-muted focus:outline-none focus:border-primary/80 transition-all"
        />
      </div>

      {/* Rooms List */}
      <div className="flex flex-col space-y-3">
        {isLoading ? (
          <div className="flex flex-col items-center justify-center py-16 space-y-2">
            <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
            <span className="text-xs text-content-muted">Discovering active rooms...</span>
          </div>
        ) : rooms.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center space-y-3">
            <Radio className="w-12 h-12 text-content-muted/30" />
            <span className="text-base font-semibold text-content-secondary">
              No active rooms right now
            </span>
            <p className="text-xs text-content-muted max-w-xs">
              Be the first to start a listening session and invite your friends.
            </p>
            <button
              onClick={() => setIsCreateModalOpen(true)}
              className="mt-2 px-4 py-2 rounded-xl bg-surface-hover border border-border text-xs font-semibold text-content-primary hover:border-primary"
            >
              Start Room
            </button>
          </div>
        ) : (
          rooms.map((room) => <RoomCard key={room.id} room={room} />)
        )}
      </div>

      {/* ─── CREATE ROOM MODAL ──────────────────────────────────────────────── */}
      {isCreateModalOpen && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-end sm:items-center justify-center p-4 animate-fade-in">
          <div className="w-full max-w-sm bg-surface border border-border rounded-3xl p-6 shadow-2xl animate-slide-up">
            <div className="flex items-center justify-between pb-4 border-b border-border/60">
              <h2 className="text-lg font-bold text-content-primary">Create Listening Room</h2>
              <button
                onClick={() => setIsCreateModalOpen(false)}
                className="w-8 h-8 flex items-center justify-center rounded-full bg-white/5 text-content-secondary"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleCreateRoom} className="flex flex-col space-y-4 mt-4">
              <div>
                <label className="text-xs font-semibold text-content-secondary block mb-1.5">
                  Room Name
                </label>
                <input
                  type="text"
                  required
                  value={newRoomName}
                  onChange={(e) => setNewRoomName(e.target.value)}
                  placeholder="e.g. Midnight Cyber Lounge"
                  className="w-full px-3.5 py-2.5 rounded-xl bg-surface-light border border-border text-sm text-content-primary placeholder:text-content-muted focus:outline-none focus:border-primary"
                />
              </div>

              <div className="flex items-center justify-between p-3 rounded-xl bg-surface-light border border-border/40">
                <div className="flex flex-col">
                  <span className="text-xs font-bold text-content-primary">Public Room</span>
                  <span className="text-[11px] text-content-muted">
                    Visible in Global Rooms discovery
                  </span>
                </div>
                <input
                  type="checkbox"
                  checked={isPublic}
                  onChange={(e) => setIsPublic(e.target.checked)}
                  className="w-4 h-4 accent-primary rounded cursor-pointer"
                />
              </div>

              {createError && (
                <div className="p-2.5 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-xs">
                  {createError}
                </div>
              )}

              <button
                type="submit"
                disabled={isSubmitting || !newRoomName.trim()}
                className="w-full py-3 rounded-xl bg-primary hover:bg-primary-hover text-white font-semibold text-sm shadow-lg shadow-primary/25 disabled:opacity-50 transition-all active:scale-[0.98]"
              >
                {isSubmitting ? "Creating..." : "Launch Room"}
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
