/**
 * Centralized Telegram configuration and deep link generators for Afterhours.
 */

import { api } from "./api";

const DEFAULT_BOT_USERNAME = "afterhoursfmbot";

let cachedBotUsername: string | null = null;
let configPromise: Promise<string> | null = null;

/**
 * Returns the currently known bot username synchronously.
 * Falls back to environment variable, or "afterhoursfmbot".
 */
export function getTelegramBotUsername(): string {
  if (cachedBotUsername) {
    return cachedBotUsername;
  }
  const envBot =
    process.env.NEXT_PUBLIC_TELEGRAM_BOT_USERNAME ||
    process.env.NEXT_PUBLIC_BOT_USERNAME;
  if (envBot && envBot.trim() && envBot !== "AfterhoursAudioBot") {
    return envBot.trim();
  }
  return DEFAULT_BOT_USERNAME;
}

/**
 * Asynchronously fetches public config from backend and caches the bot username.
 */
export async function fetchPublicConfig(): Promise<string> {
  if (cachedBotUsername) return cachedBotUsername;
  if (configPromise) return configPromise;

  configPromise = (async () => {
    try {
      const res = await api.config.get();
      if (res.bot_username && typeof res.bot_username === "string") {
        cachedBotUsername = res.bot_username.trim();
        return cachedBotUsername;
      }
    } catch {
      // Keep fallback
    }
    return getTelegramBotUsername();
  })();

  return configPromise;
}

/**
 * Builds the official Telegram Mini App deep link for a track.
 * Format: https://t.me/<bot_username>?startapp=track_<track_id>
 */
export function getTrackShareUrl(trackId: string): string {
  const bot = getTelegramBotUsername();
  return `https://t.me/${bot}?startapp=track_${encodeURIComponent(trackId)}`;
}

/**
 * Builds the official Telegram Mini App deep link for a room invitation.
 * Format: https://t.me/<bot_username>?startapp=room_<room_id>
 */
export function getRoomInviteUrl(roomId: string): string {
  const bot = getTelegramBotUsername();
  return `https://t.me/${bot}?startapp=room_${encodeURIComponent(roomId)}`;
}

/**
 * Builds a Telegram share URL to open Telegram's forward dialog with prefilled text and URL.
 */
export function getTelegramShareDialogUrl(url: string, text?: string): string {
  const params = new URLSearchParams({ url });
  if (text) {
    params.set("text", text);
  }
  return `https://t.me/share/url?${params.toString()}`;
}
