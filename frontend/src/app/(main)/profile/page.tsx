"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  User,
  Upload,
  Clock,
  Settings,
  Disc3,
  Heart,
  ListMusic,
  CheckCircle2,
  Trash2,
  ShieldCheck,
  X,
  Play,
} from "lucide-react";
import { Track } from "@/lib/types";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/auth-context";
import { usePlayer } from "@/contexts/player-context";
import { useTelegram } from "@/contexts/telegram-context";
import { TrackArtwork } from "@/components/ui/track-artwork";

export default function ProfilePage() {
  const { user, isGuest, isAuthenticated, isInitializing, updateProfile, logout } = useAuth();
  const { playTrack } = usePlayer();
  const { haptic } = useTelegram();

  const [activeTab, setActiveTab] = useState<"uploads" | "history">("uploads");
  const [uploads, setUploads] = useState<Track[]>([]);
  const [history, setHistory] = useState<{ history_id: string; played_at: string; track: Track }[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Upload modal state
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [hasPermission, setHasPermission] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadSuccess, setUploadSuccess] = useState("");

  // Edit settings modal
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [editDisplayName, setEditDisplayName] = useState(user?.display_name || "");
  const [editBio, setEditBio] = useState(user?.bio || "");
  const [editGenderPublic, setEditGenderPublic] = useState(user?.gender_public || false);

  const loadUserData = async () => {
    try {
      setIsLoading(true);
      const [userUploads, userHistory] = await Promise.all([
        api.tracks.myUploads().catch(() => []),
        api.history.list(30).catch(() => []),
      ]);
      setUploads(userUploads);
      setHistory(userHistory);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (isInitializing) return;
    if (isAuthenticated) {
      loadUserData();
    } else {
      setUploads([]);
      setHistory([]);
      setIsLoading(false);
    }
  }, [isInitializing, isAuthenticated, user]);

  const handleUploadSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedFile || !hasPermission || isUploading) return;

    haptic("medium");
    setIsUploading(true);
    setUploadError("");
    setUploadSuccess("");

    try {
      const res = await api.uploads.file(selectedFile, hasPermission);
      setUploadSuccess(res.message);
      setSelectedFile(null);
      setHasPermission(false);
      loadUserData();
      setTimeout(() => {
        setIsUploadModalOpen(false);
        setUploadSuccess("");
      }, 1500);
    } catch (err: any) {
      setUploadError(err.message || "Failed to upload file");
    } finally {
      setIsUploading(false);
    }
  };

  const handleDeleteUpload = async (trackId: string) => {
    if (!confirm("Delete this track? Future streaming and room access will be revoked.")) return;
    haptic("medium");
    try {
      await api.tracks.delete(trackId);
      loadUserData();
    } catch (err: any) {
      alert(err.message);
    }
  };

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    haptic("light");
    try {
      await updateProfile({
        display_name: editDisplayName.trim(),
        bio: editBio.trim(),
        gender_public: editGenderPublic,
      });
      setIsEditModalOpen(false);
    } catch (err: any) {
      alert(err.message);
    }
  };

  return (
    <div className="flex flex-col space-y-5 pb-8">
      {/* ─── PROFILE HEADER ───────────────────────────────────────────────── */}
      <div className="flex flex-col items-center text-center p-6 rounded-3xl bg-surface/80 border border-border shadow-xl">
        <div className="relative w-20 h-20 rounded-full overflow-hidden bg-surface-light border-2 border-primary/40 shadow-lg flex items-center justify-center mb-3">
          {user?.avatar_url ? (
            <img src={user.avatar_url} alt="" className="w-full h-full object-cover" />
          ) : (
            <User className="w-9 h-9 text-content-muted" />
          )}
        </div>

        <h1 className="text-xl font-extrabold text-content-primary">
          {user?.display_name || "Listener"}
        </h1>
        <span className="text-xs font-mono text-content-secondary mt-0.5">
          @{user?.username || "listener"}
        </span>

        {user?.bio && (
          <p className="text-xs text-content-secondary max-w-xs mt-2">
            {user.bio}
          </p>
        )}

        {user?.gender_public && user?.gender && (
          <span className="mt-2 px-2.5 py-0.5 rounded-full bg-accent/15 text-accent text-[10px] font-bold uppercase tracking-wider">
            {user.gender}
          </span>
        )}

        {isGuest && (
          <div className="mt-3 px-3 py-1 rounded-full bg-primary/20 border border-primary/40 text-[11px] font-semibold text-primary">
            Guest Mode (limited permissions)
          </div>
        )}

        {/* Stats Row */}
        <div className="flex items-center justify-around w-full mt-6 pt-5 border-t border-border/40">
          <div className="flex flex-col items-center">
            <span className="text-base font-extrabold text-content-primary">
              {uploads.length}
            </span>
            <span className="text-[11px] text-content-muted">Uploads</span>
          </div>
          <div className="w-[1px] h-6 bg-border/40" />
          <div className="flex flex-col items-center">
            <span className="text-base font-extrabold text-content-primary">
              {user?.playlists_count ?? 0}
            </span>
            <span className="text-[11px] text-content-muted">Playlists</span>
          </div>
          <div className="w-[1px] h-6 bg-border/40" />
          <div className="flex flex-col items-center">
            <span className="text-base font-extrabold text-content-primary">
              {user?.favorites_count ?? 0}
            </span>
            <span className="text-[11px] text-content-muted">Favorites</span>
          </div>
        </div>

        {/* Edit Button */}
        <button
          onClick={() => {
            haptic("light");
            setEditDisplayName(user?.display_name || "");
            setEditBio(user?.bio || "");
            setEditGenderPublic(user?.gender_public || false);
            setIsEditModalOpen(true);
          }}
          className="mt-4 flex items-center space-x-1 px-4 py-1.5 rounded-xl bg-surface-hover border border-border text-xs font-semibold text-content-secondary hover:text-content-primary"
        >
          <Settings className="w-3.5 h-3.5" />
          <span>Edit Profile</span>
        </button>
      </div>

      {/* ─── TABS: MY UPLOADS & LISTENING HISTORY ───────────────────────────── */}
      <div className="flex items-center justify-between border-b border-border/40 pb-2">
        <div className="flex items-center space-x-2">
          <button
            onClick={() => {
              haptic("light");
              setActiveTab("uploads");
            }}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
              activeTab === "uploads"
                ? "bg-primary text-white"
                : "text-content-secondary hover:text-content-primary"
            }`}
          >
            <Upload className="w-3.5 h-3.5" />
            <span>My Uploads ({uploads.length})</span>
          </button>

          <button
            onClick={() => {
              haptic("light");
              setActiveTab("history");
            }}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
              activeTab === "history"
                ? "bg-primary text-white"
                : "text-content-secondary hover:text-content-primary"
            }`}
          >
            <Clock className="w-3.5 h-3.5" />
            <span>History</span>
          </button>
        </div>

        {activeTab === "uploads" && (
          <button
            onClick={() => {
              haptic("light");
              setIsUploadModalOpen(true);
            }}
            className="flex items-center space-x-1 px-3 py-1.5 rounded-full bg-primary/20 text-primary border border-primary/30 text-xs font-bold hover:bg-primary/30"
          >
            <span>+ Add File</span>
          </button>
        )}
      </div>

      {/* ─── UPLOADS LIST ─────────────────────────────────────────────────── */}
      {activeTab === "uploads" && (
        <div className="flex flex-col space-y-2">
          {uploads.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-center space-y-2">
              <Upload className="w-10 h-10 text-content-muted/30" />
              <span className="text-sm font-semibold text-content-secondary">
                No personal music files uploaded yet
              </span>
              <p className="text-xs text-content-muted max-w-xs">
                Upload your permitted FLAC or high-res audio files to stream or play in listening rooms.
              </p>
              <button
                onClick={() => setIsUploadModalOpen(true)}
                className="mt-2 px-4 py-2 rounded-xl bg-primary text-white text-xs font-semibold"
              >
                Upload Permitted Music
              </button>
            </div>
          ) : (
            uploads.map((t) => (
              <div
                key={t.id}
                className="flex items-center justify-between p-3 rounded-2xl bg-surface/60 border border-border/40 hover:bg-surface/90"
              >
                <div
                  onClick={() => playTrack(t, uploads)}
                  className="flex items-center space-x-3 overflow-hidden flex-1 cursor-pointer"
                >
                  <div className="w-11 h-11 rounded-xl overflow-hidden border border-border/40 flex-shrink-0">
                    <TrackArtwork
                      src={t.artwork_thumb_url || t.artwork_url}
                      alt={t.title}
                    />
                  </div>
                  <div className="flex flex-col min-w-0 pr-2">
                    <span className="text-sm font-bold text-content-primary truncate">{t.title}</span>
                    <span className="text-xs text-content-secondary truncate">
                      {t.artist_name} • {t.codec.toUpperCase()}
                    </span>
                  </div>
                </div>

                <div className="flex items-center space-x-1">
                  <button
                    onClick={() => playTrack(t, uploads)}
                    className="w-8 h-8 flex items-center justify-center rounded-full bg-white/5 hover:bg-primary hover:text-white text-content-secondary"
                  >
                    <Play className="w-3.5 h-3.5 fill-current ml-0.5" />
                  </button>
                  <button
                    onClick={() => handleDeleteUpload(t.id)}
                    className="w-8 h-8 flex items-center justify-center rounded-full text-content-muted hover:text-red-400"
                    title="Delete upload"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* ─── HISTORY LIST ─────────────────────────────────────────────────── */}
      {activeTab === "history" && (
        <div className="flex flex-col space-y-2">
          {history.length === 0 ? (
            <div className="py-12 text-center text-xs text-content-muted">
              No recent listening history.
            </div>
          ) : (
            history.map((item) => (
              <div
                key={item.history_id}
                onClick={() => playTrack(item.track)}
                className="flex items-center justify-between p-2.5 rounded-xl bg-surface/50 border border-border/40 cursor-pointer hover:bg-surface/80"
              >
                <div className="flex items-center space-x-3 overflow-hidden">
                  <div className="w-9 h-9 rounded-lg overflow-hidden border border-border/40 flex-shrink-0">
                    <TrackArtwork
                      src={item.track.artwork_thumb_url || item.track.artwork_url}
                      alt={item.track.title}
                    />
                  </div>
                  <div className="flex flex-col min-w-0">
                    <span className="text-xs font-semibold text-content-primary truncate">
                      {item.track.title}
                    </span>
                    <span className="text-[11px] text-content-secondary truncate">
                      {item.track.artist_name}
                    </span>
                  </div>
                </div>

                <span className="text-[10px] font-mono text-content-muted">
                  {new Date(item.played_at).toLocaleDateString()}
                </span>
              </div>
            ))
          )}
        </div>
      )}

      {/* ─── UPLOAD MODAL ─────────────────────────────────────────────────── */}
      {isUploadModalOpen && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-end sm:items-center justify-center p-4">
          <div className="w-full max-w-sm bg-surface border border-border rounded-3xl p-6 shadow-2xl animate-slide-up">
            <div className="flex items-center justify-between pb-3 border-b border-border/60">
              <h2 className="text-base font-bold text-content-primary">Upload Music File</h2>
              <button
                onClick={() => setIsUploadModalOpen(false)}
                className="w-7 h-7 flex items-center justify-center rounded-full bg-white/5 text-content-secondary"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleUploadSubmit} className="flex flex-col space-y-4 mt-4">
              <div>
                <label className="text-xs font-semibold text-content-secondary block mb-1.5">
                  Audio File (FLAC, MP3, M4A, WAV up to 100MB)
                </label>
                <input
                  type="file"
                  accept=".flac,.mp3,.m4a,.aac,.ogg,.wav"
                  required
                  onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
                  className="w-full text-xs text-content-secondary file:mr-3 file:py-2 file:px-3 file:rounded-xl file:border-0 file:text-xs file:font-semibold file:bg-primary file:text-white hover:file:bg-primary-hover cursor-pointer"
                />
              </div>

              {/* Rights confirmation checkbox */}
              <div className="p-3 rounded-2xl bg-surface-light border border-border/60 flex items-start space-x-3">
                <input
                  type="checkbox"
                  id="rights-confirm"
                  checked={hasPermission}
                  onChange={(e) => setHasPermission(e.target.checked)}
                  className="mt-0.5 w-4 h-4 accent-primary rounded cursor-pointer flex-shrink-0"
                />
                <label htmlFor="rights-confirm" className="text-[11px] text-content-secondary leading-tight cursor-pointer">
                  I confirm that I hold the legal right or permission to store and share this audio file on Afterhours.
                </label>
              </div>

              {uploadError && (
                <div className="text-xs text-red-400 bg-red-500/10 p-2.5 rounded-xl border border-red-500/20">
                  {uploadError}
                </div>
              )}

              {uploadSuccess && (
                <div className="text-xs text-green-400 bg-green-500/10 p-2.5 rounded-xl border border-green-500/20">
                  {uploadSuccess}
                </div>
              )}

              <button
                type="submit"
                disabled={isUploading || !selectedFile || !hasPermission}
                className="w-full py-3 rounded-xl bg-primary hover:bg-primary-hover text-white font-bold text-xs shadow-lg shadow-primary/25 disabled:opacity-50 transition-all"
              >
                {isUploading ? "Ingesting & Transcoding..." : "Upload & Ingest to Vault"}
              </button>
            </form>
          </div>
        </div>
      )}

      {/* ─── EDIT PROFILE MODAL ───────────────────────────────────────────── */}
      {isEditModalOpen && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-end sm:items-center justify-center p-4">
          <div className="w-full max-w-sm bg-surface border border-border rounded-3xl p-6 shadow-2xl animate-slide-up">
            <div className="flex items-center justify-between pb-3 border-b border-border/60">
              <h2 className="text-base font-bold text-content-primary">Edit Profile</h2>
              <button
                onClick={() => setIsEditModalOpen(false)}
                className="w-7 h-7 flex items-center justify-center rounded-full bg-white/5 text-content-secondary"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleSaveProfile} className="flex flex-col space-y-4 mt-4">
              <div>
                <label className="text-xs font-semibold text-content-secondary block mb-1">
                  Display Name
                </label>
                <input
                  type="text"
                  required
                  value={editDisplayName}
                  onChange={(e) => setEditDisplayName(e.target.value)}
                  className="w-full px-3 py-2 rounded-xl bg-surface-light border border-border text-sm text-content-primary focus:outline-none focus:border-primary"
                />
              </div>

              <div>
                <label className="text-xs font-semibold text-content-secondary block mb-1">
                  Bio
                </label>
                <textarea
                  rows={3}
                  value={editBio}
                  onChange={(e) => setEditBio(e.target.value)}
                  className="w-full px-3 py-2 rounded-xl bg-surface-light border border-border text-sm text-content-primary focus:outline-none focus:border-primary resize-none"
                />
              </div>

              <div className="flex items-center justify-between p-3 rounded-xl bg-surface-light border border-border/40">
                <div className="flex flex-col">
                  <span className="text-xs font-bold text-content-primary">Display Gender Publicly</span>
                  <span className="text-[11px] text-content-muted">Private by default</span>
                </div>
                <input
                  type="checkbox"
                  checked={editGenderPublic}
                  onChange={(e) => setEditGenderPublic(e.target.checked)}
                  className="w-4 h-4 accent-primary rounded cursor-pointer"
                />
              </div>

              <button
                type="submit"
                className="w-full py-2.5 rounded-xl bg-primary hover:bg-primary-hover text-white font-bold text-xs shadow-lg shadow-primary/25"
              >
                Save Changes
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
