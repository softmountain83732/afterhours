"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { User, Sparkles, Check, AlertCircle } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/auth-context";
import { useTelegram } from "@/contexts/telegram-context";

export default function SetupPage() {
  const router = useRouter();
  const { user, refreshUser } = useAuth();
  const { haptic } = useTelegram();

  const [displayName, setDisplayName] = useState(user?.display_name || "");
  const [username, setUsername] = useState(user?.username || "");
  const [gender, setGender] = useState("");
  const [genderPublic, setGenderPublic] = useState(false);
  const [bio, setBio] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  React.useEffect(() => {
    if (user?.display_name && !displayName) {
      setDisplayName(user.display_name);
    }
    if (user?.username && !username) {
      setUsername(user.username);
    }
  }, [user]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!displayName.trim() || !username.trim() || isSubmitting) return;

    haptic("medium");
    setIsSubmitting(true);
    setError("");

    try {
      await api.profiles.setup({
        display_name: displayName.trim(),
        username: username.trim().toLowerCase(),
        bio: bio.trim(),
        gender: gender ? gender : undefined,
        gender_public: genderPublic,
      });

      await refreshUser();
      router.push("/library");
    } catch (err: any) {
      setError(err.message || "Failed to set up profile");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-background flex flex-col items-center justify-center p-6 text-content-primary">
      <div className="w-full max-w-sm flex flex-col space-y-6 bg-surface/90 border border-border/80 rounded-3xl p-6 shadow-2xl">
        <div className="flex flex-col items-center text-center">
          <div className="w-12 h-12 rounded-2xl bg-primary/20 border border-primary/30 flex items-center justify-center text-primary mb-3 shadow-lg">
            <Sparkles className="w-6 h-6" />
          </div>
          <h1 className="text-2xl font-extrabold tracking-tight">Welcome to Afterhours</h1>
          <p className="text-xs text-content-secondary mt-1">
            Complete your profile to start listening and sharing music.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col space-y-4">
          {/* Display Name */}
          <div>
            <label className="text-xs font-semibold text-content-secondary block mb-1">
              Display Name *
            </label>
            <input
              type="text"
              required
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="e.g. Maya Lin"
              className="w-full px-3.5 py-2.5 rounded-xl bg-surface-light border border-border text-sm text-content-primary placeholder:text-content-muted focus:outline-none focus:border-primary"
            />
          </div>

          {/* Username */}
          <div>
            <label className="text-xs font-semibold text-content-secondary block mb-1">
              Username (Unique) *
            </label>
            <div className="relative flex items-center">
              <span className="absolute left-3.5 text-xs text-content-muted">@</span>
              <input
                type="text"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, ""))}
                placeholder="mayalin"
                className="w-full pl-8 pr-3.5 py-2.5 rounded-xl bg-surface-light border border-border text-sm text-content-primary placeholder:text-content-muted focus:outline-none focus:border-primary"
              />
            </div>
          </div>

          {/* Bio */}
          <div>
            <label className="text-xs font-semibold text-content-secondary block mb-1">
              Bio (Optional)
            </label>
            <textarea
              rows={2}
              value={bio}
              onChange={(e) => setBio(e.target.value)}
              placeholder="What genres do you listen to after hours?"
              className="w-full px-3.5 py-2 rounded-xl bg-surface-light border border-border text-sm text-content-primary placeholder:text-content-muted focus:outline-none focus:border-primary resize-none"
            />
          </div>

          {/* Gender (Optional) */}
          <div>
            <label className="text-xs font-semibold text-content-secondary block mb-1">
              Gender (Optional)
            </label>
            <select
              value={gender}
              onChange={(e) => setGender(e.target.value)}
              className="w-full px-3.5 py-2.5 rounded-xl bg-surface-light border border-border text-sm text-content-primary focus:outline-none focus:border-primary"
            >
              <option value="">Prefer not to say</option>
              <option value="female">Female</option>
              <option value="male">Male</option>
              <option value="non-binary">Non-binary</option>
              <option value="other">Other</option>
            </select>
          </div>

          {/* Gender privacy toggle */}
          {gender && (
            <div className="flex items-center justify-between p-3 rounded-xl bg-surface-light border border-border/40">
              <div className="flex flex-col">
                <span className="text-xs font-semibold text-content-primary">
                  Display gender publicly
                </span>
                <span className="text-[10px] text-content-muted">
                  Private by default
                </span>
              </div>
              <input
                type="checkbox"
                checked={genderPublic}
                onChange={(e) => setGenderPublic(e.target.checked)}
                className="w-4 h-4 accent-primary rounded cursor-pointer"
              />
            </div>
          )}

          {error && (
            <div className="flex items-center space-x-2 text-xs text-red-400 bg-red-500/10 p-2.5 rounded-xl border border-red-500/20">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={isSubmitting || !displayName.trim() || !username.trim()}
            className="w-full py-3 rounded-xl bg-primary hover:bg-primary-hover text-white font-bold text-xs shadow-lg shadow-primary/25 disabled:opacity-50 transition-all active:scale-[0.98]"
          >
            {isSubmitting ? "Setting up..." : "Enter Afterhours"}
          </button>
        </form>
      </div>
    </div>
  );
}
