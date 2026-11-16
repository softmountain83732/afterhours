"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Sparkles, Heart, Upload, Disc3 } from "lucide-react";
import { SearchBar } from "@/components/ui/search-bar";
import { TrackCard } from "@/components/ui/track-card";
import { Track } from "@/lib/types";
import { api } from "@/lib/api";
import { usePlayer } from "@/contexts/player-context";
import { useTelegram } from "@/contexts/telegram-context";

export default function LibraryPage() {
  const [query, setQuery] = useState("");
  const [tracks, setTracks] = useState<Track[]>([]);
  const [favorites, setFavorites] = useState<Track[]>([]);
  const [activeTab, setActiveTab] = useState<"all" | "favorites">("all");
  const [isLoading, setIsLoading] = useState(true);

  const { playTrack } = usePlayer();
  const { haptic } = useTelegram();

  const loadTracks = async (searchTerm = "") => {
    try {
      setIsLoading(true);
      const res = await api.tracks.list(searchTerm);
      setTracks(res.items);
    } catch {
      // ignore
    } finally {
      setIsLoading(false);
    }
  };

  const loadFavorites = async () => {
    try {
      const favs = await api.favorites.list();
      setFavorites(favs);
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    loadTracks();
    loadFavorites();
  }, []);

  const handleSearch = (val: string) => {
    setQuery(val);
    loadTracks(val);
  };

  const displayTracks = activeTab === "all" ? tracks : favorites;

  return (
    <div className="flex flex-col space-y-5">
      {/* Header */}
      <header className="flex items-center justify-between pt-2">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-content-primary">
            Library
          </h1>
          <p className="text-xs text-content-secondary mt-0.5">
            Your music. Your moments.
          </p>
        </div>

        <Link
          href="/profile#uploads"
          onClick={() => haptic("light")}
          className="flex items-center space-x-1.5 px-3 py-1.5 rounded-full bg-surface-hover border border-border text-xs font-semibold text-content-primary hover:border-primary/50 transition-colors"
        >
          <Upload className="w-3.5 h-3.5 text-accent" />
          <span>Upload</span>
        </Link>
      </header>

      {/* Search Bar */}
      <SearchBar value={query} onChange={handleSearch} />

      {/* Tabs */}
      <div className="flex items-center space-x-2 border-b border-border/40 pb-2">
        <button
          onClick={() => {
            haptic("light");
            setActiveTab("all");
          }}
          className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
            activeTab === "all"
              ? "bg-primary text-white"
              : "text-content-secondary hover:text-content-primary"
          }`}
        >
          <Disc3 className="w-3.5 h-3.5" />
          <span>Explore ({tracks.length})</span>
        </button>

        <button
          onClick={() => {
            haptic("light");
            setActiveTab("favorites");
            loadFavorites();
          }}
          className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
            activeTab === "favorites"
              ? "bg-primary text-white"
              : "text-content-secondary hover:text-content-primary"
          }`}
        >
          <Heart className="w-3.5 h-3.5 fill-current" />
          <span>Favorites ({favorites.length})</span>
        </button>
      </div>

      {/* Track List */}
      <div className="flex flex-col space-y-1">
        {isLoading ? (
          <div className="flex flex-col items-center justify-center py-16 space-y-2">
            <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
            <span className="text-xs text-content-muted">Loading music collection...</span>
          </div>
        ) : displayTracks.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center space-y-2">
            <Disc3 className="w-10 h-10 text-content-muted/40" />
            <span className="text-sm font-semibold text-content-secondary">
              {activeTab === "favorites" ? "No favorite tracks yet" : "No tracks found"}
            </span>
            <span className="text-xs text-content-muted max-w-xs">
              {activeTab === "favorites"
                ? "Tap the heart icon on any song to add it here."
                : "Try searching for another artist or song name."}
            </span>
          </div>
        ) : (
          displayTracks.map((track, idx) => (
            <TrackCard
              key={track.id}
              track={track}
              index={idx}
              onSelect={() => playTrack(track, displayTracks)}
            />
          ))
        )}
      </div>
    </div>
  );
}
