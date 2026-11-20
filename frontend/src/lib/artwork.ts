/**
 * Centralized artwork resolution and fallback management for Afterhours.
 */

const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export const DEFAULT_FALLBACK_ARTWORK_URL = "/default-artwork.svg";

/**
 * Standard data-URI for the Afterhours dark luxury vinyl cover.
 * Guarantees instantaneous, zero-network fallback display across all environments.
 */
export const DEFAULT_ARTWORK_DATA_URI =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 500" width="100%" height="100%">
  <defs>
    <radialGradient id="bg" cx="50%" cy="50%" r="70%">
      <stop offset="0%" stop-color="#1e1838"/>
      <stop offset="50%" stop-color="#120e24"/>
      <stop offset="100%" stop-color="#08070d"/>
    </radialGradient>
    <radialGradient id="glow" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#a855f7" stop-opacity="0.4"/>
      <stop offset="60%" stop-color="#6366f1" stop-opacity="0.15"/>
      <stop offset="100%" stop-color="#000000" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="discGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#2a2347"/>
      <stop offset="100%" stop-color="#110d1f"/>
    </linearGradient>
    <linearGradient id="accentGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#c084fc"/>
      <stop offset="50%" stop-color="#818cf8"/>
      <stop offset="100%" stop-color="#38bdf8"/>
    </linearGradient>
  </defs>
  <rect width="500" height="500" rx="36" fill="url(#bg)"/>
  <circle cx="250" cy="250" r="220" fill="url(#glow)"/>
  <circle cx="250" cy="250" r="180" fill="url(#discGrad)" stroke="#382d5e" stroke-width="2"/>
  <circle cx="250" cy="250" r="160" fill="none" stroke="#251d42" stroke-width="1.5" stroke-dasharray="8 4"/>
  <circle cx="250" cy="250" r="140" fill="none" stroke="#221a3c" stroke-width="1.5"/>
  <circle cx="250" cy="250" r="120" fill="none" stroke="#2a204d" stroke-width="1.5" stroke-dasharray="12 6"/>
  <circle cx="250" cy="250" r="100" fill="none" stroke="#221a3c" stroke-width="1.5"/>
  <circle cx="250" cy="250" r="75" fill="#140f26" stroke="url(#accentGrad)" stroke-width="3"/>
  <circle cx="250" cy="250" r="68" fill="none" stroke="#3b2b68" stroke-width="1"/>
  <g transform="translate(250, 250)">
    <circle cx="0" cy="0" r="12" fill="#090810" stroke="url(#accentGrad)" stroke-width="2"/>
    <path d="M-36 -8 L-36 8 M-24 -18 L-24 18 M-12 -28 L-12 28 M12 -28 L12 28 M24 -18 L24 18 M36 -8 L36 8" 
          stroke="url(#accentGrad)" stroke-width="3.5" stroke-linecap="round"/>
  </g>
  <text x="250" y="445" text-anchor="middle" fill="#94a3b8" font-family="system-ui, -apple-system, sans-serif" font-size="14" font-weight="700" letter-spacing="6">AFTERHOURS</text>
</svg>`);

/**
 * Resolve an incoming track or album artwork URL to an absolute or reliable URL.
 * Prefixes backend API_BASE for relative `/api/static/...` paths, strips broken URLs,
 * and falls back to Afterhours default artwork.
 */
export function resolveArtworkUrl(rawUrl?: string | null): string {
  if (!rawUrl || typeof rawUrl !== "string") {
    return DEFAULT_FALLBACK_ARTWORK_URL;
  }

  const trimmed = rawUrl.trim();
  if (!trimmed || trimmed === "null" || trimmed === "undefined") {
    return DEFAULT_FALLBACK_ARTWORK_URL;
  }

  // Legacy Unsplash fallback replacement
  if (trimmed.includes("images.unsplash.com")) {
    return DEFAULT_FALLBACK_ARTWORK_URL;
  }

  // If already relative to frontend root
  if (trimmed.startsWith("/default-artwork.svg")) {
    return trimmed;
  }

  // If backend relative static path
  if (trimmed.startsWith("/api/static/artwork/default.svg")) {
    return DEFAULT_FALLBACK_ARTWORK_URL;
  }

  if (trimmed.startsWith("/api/")) {
    // If running in browser and NEXT_PUBLIC_API_URL is configured, route to backend API
    if (API_BASE && !API_BASE.includes("localhost:8000")) {
      return `${API_BASE}${trimmed}`;
    }
    return trimmed;
  }

  return trimmed;
}
