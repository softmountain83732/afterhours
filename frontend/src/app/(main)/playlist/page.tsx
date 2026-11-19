"use client";

import React, { useEffect, useState } from "react";
import { Plus, ListMusic, X } from "lucide-react";
import { PlaylistCard } from "@/components/ui/playlist-card";
import { Playlist } from "@/lib/types";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/auth-context";
import { useTelegram } from "@/contexts/telegram-context";

export default function PlaylistsPage() {
  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isPublic, setIsPublic] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const { isAuthenticated, isInitializing, isGuest } = useAuth();
  const { haptic } = useTelegram();

  const loadPlaylists = async () => {
    try {
      setIsLoading(true);
      const res = await api.playlists.list();
      setPlaylists(res);
    } catch {
      // ignore
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (isInitializing) return;
    if (isAuthenticated) {
      loadPlaylists();
    } else {
      setPlaylists([]);
      setIsLoading(false);
    }
  }, [isInitializing, isAuthenticated]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || isSubmitting) return;

    haptic("medium");
    setIsSubmitting(true);
    try {
      await api.playlists.create({
        name: name.trim(),
        description: description.trim(),
        is_public: isPublic,
      });
      setIsModalOpen(false);
      setName("");
      setDescription("");
      loadPlaylists();
    } catch (err: any) {
      alert(err.message || "Failed to create playlist");
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
            Playlists
          </h1>
          <p className="text-xs text-content-secondary mt-0.5">
            Your personal collections & curation.
          </p>
        </div>

        <button
          onClick={() => {
            haptic("light");
            setIsModalOpen(true);
          }}
          className="flex items-center space-x-1.5 px-3.5 py-1.5 rounded-full bg-primary hover:bg-primary-hover text-white text-xs font-semibold shadow-lg shadow-primary/25 transition-all active:scale-95"
        >
          <Plus className="w-4 h-4" />
          <span>New Playlist</span>
        </button>
      </header>

      {/* Grid */}
      {isLoading ? (
        <div className="flex flex-col items-center justify-center py-16 space-y-2">
          <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
          <span className="text-xs text-content-muted">Loading your playlists...</span>
        </div>
      ) : playlists.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center space-y-3">
          <ListMusic className="w-12 h-12 text-content-muted/30" />
          <span className="text-base font-semibold text-content-secondary">
            No playlists yet
          </span>
          <p className="text-xs text-content-muted max-w-xs">
            Create playlists to organize your favorite tracks for late night listening.
          </p>
          <button
            onClick={() => setIsModalOpen(true)}
            className="mt-2 px-4 py-2 rounded-xl bg-surface-hover border border-border text-xs font-semibold text-content-primary hover:border-primary"
          >
            Create Playlist
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4">
          {playlists.map((pl) => (
            <PlaylistCard key={pl.id} playlist={pl} />
          ))}
        </div>
      )}

      {/* ─── CREATE MODAL ─────────────────────────────────────────────────── */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-end sm:items-center justify-center p-4 animate-fade-in">
          <div className="w-full max-w-sm bg-surface border border-border rounded-3xl p-6 shadow-2xl animate-slide-up">
            <div className="flex items-center justify-between pb-4 border-b border-border/60">
              <h2 className="text-lg font-bold text-content-primary">New Playlist</h2>
              <button
                onClick={() => setIsModalOpen(false)}
                className="w-8 h-8 flex items-center justify-center rounded-full bg-white/5 text-content-secondary"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleCreate} className="flex flex-col space-y-4 mt-4">
              <div>
                <label className="text-xs font-semibold text-content-secondary block mb-1.5">
                  Playlist Title
                </label>
                <input
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. 3 AM Drive"
                  className="w-full px-3.5 py-2.5 rounded-xl bg-surface-light border border-border text-sm text-content-primary placeholder:text-content-muted focus:outline-none focus:border-primary"
                />
              </div>

              <div>
                <label className="text-xs font-semibold text-content-secondary block mb-1.5">
                  Description (Optional)
                </label>
                <input
                  type="text"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Deep atmospheric tracks..."
                  className="w-full px-3.5 py-2.5 rounded-xl bg-surface-light border border-border text-sm text-content-primary placeholder:text-content-muted focus:outline-none focus:border-primary"
                />
              </div>

              <div className="flex items-center justify-between p-3 rounded-xl bg-surface-light border border-border/40">
                <div className="flex flex-col">
                  <span className="text-xs font-bold text-content-primary">Public Playlist</span>
                  <span className="text-[11px] text-content-muted">
                    Visible on your public profile
                  </span>
                </div>
                <input
                  type="checkbox"
                  checked={isPublic}
                  onChange={(e) => setIsPublic(e.target.checked)}
                  className="w-4 h-4 accent-primary rounded cursor-pointer"
                />
              </div>

              <button
                type="submit"
                disabled={isSubmitting || !name.trim()}
                className="w-full py-3 rounded-xl bg-primary hover:bg-primary-hover text-white font-semibold text-sm shadow-lg shadow-primary/25 disabled:opacity-50 transition-all active:scale-[0.98]"
              >
                {isSubmitting ? "Creating..." : "Save Playlist"}
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
