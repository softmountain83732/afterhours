"use client";

import React, { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { Heart, Upload, Disc3, X, Filter } from "lucide-react";
import { SearchBar } from "@/components/ui/search-bar";
import { TrackCard } from "@/components/ui/track-card";
import { TrackContextMenu } from "@/components/ui/track-context-menu";
import { Track } from "@/lib/types";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/auth-context";
import { usePlayer } from "@/contexts/player-context";
import { useTelegram } from "@/contexts/telegram-context";

export default function LibraryPage() {
  const [query, setQuery] = useState("");
  const [tracks, setTracks] = useState<Track[]>([]);
  const [favorites, setFavorites] = useState<Track[]>([]);
  const [activeTab, setActiveTab] = useState<"all" | "favorites">("all");
  const [isLoading, setIsLoading] = useState(true);
  const [activeFilter, setActiveFilter] = useState<{ type: "artist" | "album"; value: string } | null>(null);

  // Single context menu coordinator for the page
  const [contextMenuTrack, setContextMenuTrack] = useState<Track | null>(null);

  const { isAuthenticated, isInitializing, isGuest } = useAuth();
  const { playTrack } = usePlayer();
  const { haptic } = useTelegram();

  const loadTracks = useCallback(async (searchTerm = "") => {
    try {
      setIsLoading(true);
      const res = await api.tracks.list(searchTerm);
      setTracks(res.items);
    } catch {
      // ignore
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadFavorites = useCallback(async () => {
    try {
      const favs = await api.favorites.list();
      setFavorites(favs);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    loadTracks();
  }, [loadTracks]);

  useEffect(() => {
    if (isInitializing) return;
    if (isAuthenticated) {
      loadFavorites();
    } else {
      setFavorites([]);
    }
  }, [isInitializing, isAuthenticated, loadFavorites]);

  const handleSearch = (val: string) => {
    setQuery(val);
    setActiveFilter(null);
    loadTracks(val);
  };

  const handleFilterArtist = (artistName: string) => {
    setActiveFilter({ type: "artist", value: artistName });
    setQuery(artistName);
    setActiveTab("all");
    loadTracks(artistName);
  };

  const handleFilterAlbum = (albumTitle: string) => {
    setActiveFilter({ type: "album", value: albumTitle });
    setQuery(albumTitle);
    setActiveTab("all");
    loadTracks(albumTitle);
  };

  const clearActiveFilter = () => {
    haptic("light");
    setActiveFilter(null);
    setQuery("");
    loadTracks("");
  };

  const displayTracks = activeTab === "all" ? tracks : favorites;

  return (
    <div className="flex flex-col space-y-5 pb-6">
      {/* Header */}
      <header className="flex items-center justify-between pt-2">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-content-primary">
            Library
          </h1>
          <p className="text-xs text-content-secondary mt-0.5">
            Your music. Your people. Your moments.
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

      {/* Active Filter Pill */}
      {activeFilter && (
        <div className="flex items-center space-x-2">
          <div className="flex items-center space-x-2 px-3 py-1.5 rounded-full bg-surface-light border border-primary/30 text-xs text-content-primary animate-fade-in">
            <Filter className="w-3 h-3 text-primary" />
            <span className="text-content-secondary capitalize">{activeFilter.type}:</span>
            <span className="font-semibold text-primary truncate max-w-[180px]">
              {activeFilter.value}
            </span>
            <button
              onClick={clearActiveFilter}
              aria-label="Clear filter"
              className="p-0.5 rounded-full text-content-muted hover:text-white hover:bg-white/10"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}

      {/* Navigation Tabs */}
      <div className="flex items-center space-x-2 border-b border-border/40 pb-2">
        <button
          onClick={() => {
            haptic("light");
            setActiveTab("all");
          }}
          className={`flex items-center space-x-1.5 px-3.5 py-1.5 rounded-xl text-xs font-semibold transition-all ${
            activeTab === "all"
              ? "bg-primary text-white shadow-md shadow-primary/25"
              : "text-content-secondary hover:text-content-primary hover:bg-surface-light/40"
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
          className={`flex items-center space-x-1.5 px-3.5 py-1.5 rounded-xl text-xs font-semibold transition-all ${
            activeTab === "favorites"
              ? "bg-primary text-white shadow-md shadow-primary/25"
              : "text-content-secondary hover:text-content-primary hover:bg-surface-light/40"
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
            <span className="text-xs text-content-muted">Loading collection...</span>
          </div>
        ) : displayTracks.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center space-y-2.5">
            <Disc3 className="w-10 h-10 text-content-muted/40" />
            <span className="text-sm font-semibold text-content-secondary">
              {activeTab === "favorites" ? "No favorite tracks yet" : "No tracks found"}
            </span>
            <p className="text-xs text-content-muted max-w-xs">
              {activeTab === "favorites"
                ? "Tap the ⋯ menu on any track and select Add to Favorites to build your personal vault."
                : "Try searching for another artist, track title, or album."}
            </p>
            {activeFilter && (
              <button
                onClick={clearActiveFilter}
                className="mt-2 text-xs font-semibold text-primary hover:underline"
              >
                Clear current filter
              </button>
            )}
          </div>
        ) : (
          displayTracks.map((track, idx) => (
            <TrackCard
              key={track.id}
              track={track}
              index={idx}
              onSelect={() => playTrack(track, displayTracks)}
              onOpenContextMenu={(t) => setContextMenuTrack(t)}
            />
          ))
        )}
      </div>

      {/* Single Context Menu Coordinator */}
      <TrackContextMenu
        track={contextMenuTrack}
        isOpen={Boolean(contextMenuTrack)}
        onClose={() => {
          setContextMenuTrack(null);
          loadFavorites();
        }}
        onFilterArtist={handleFilterArtist}
        onFilterAlbum={handleFilterAlbum}
      />
    </div>
  );
}
