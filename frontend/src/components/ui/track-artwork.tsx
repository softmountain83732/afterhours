"use client";

import React, { useState, useEffect } from "react";
import { resolveArtworkUrl, DEFAULT_FALLBACK_ARTWORK_URL, DEFAULT_ARTWORK_DATA_URI } from "@/lib/artwork";

export interface TrackArtworkProps {
  src?: string | null;
  alt?: string;
  className?: string;
  classNameImage?: string;
  aspectRatio?: "square" | "video";
  children?: React.ReactNode;
}

export const TrackArtwork: React.FC<TrackArtworkProps> = ({
  src,
  alt = "Track artwork",
  className = "w-full h-full",
  classNameImage = "object-cover",
  children,
}) => {
  const resolved = resolveArtworkUrl(src);
  const [currentSrc, setCurrentSrc] = useState<string>(resolved);
  const [isLoaded, setIsLoaded] = useState<boolean>(false);
  const [hasError, setHasError] = useState<boolean>(false);

  useEffect(() => {
    const nextUrl = resolveArtworkUrl(src);
    setCurrentSrc(nextUrl);
    setHasError(false);
    setIsLoaded(false);
  }, [src]);

  const handleError = () => {
    if (!hasError) {
      setHasError(true);
      // Fallback directly to embedded SVG data URI so no secondary network request is needed
      setCurrentSrc(DEFAULT_ARTWORK_DATA_URI);
    }
  };

  const handleLoad = () => {
    setIsLoaded(true);
  };

  return (
    <div className={`relative overflow-hidden bg-surface-light select-none ${className}`}>
      {/* Loading placeholder skeleton */}
      {!isLoaded && !hasError && (
        <div className="absolute inset-0 bg-gradient-to-br from-surface-light via-[#181329] to-surface-light animate-pulse" />
      )}

      {/* Actual or Fallback Cover Image */}
      <img
        src={currentSrc}
        alt={alt}
        loading="lazy"
        decoding="async"
        onLoad={handleLoad}
        onError={handleError}
        className={`w-full h-full transition-opacity duration-300 ${
          isLoaded || hasError ? "opacity-100" : "opacity-0"
        } ${classNameImage}`}
      />

      {/* Optional overlays (e.g., live playing indicator, lock/globe badges) */}
      {children}
    </div>
  );
};
