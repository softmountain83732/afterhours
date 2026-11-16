"use client";

import React from "react";
import { Search, X } from "lucide-react";

interface SearchBarProps {
  value: string;
  onChange: (val: string) => void;
  placeholder?: string;
}

export const SearchBar: React.FC<SearchBarProps> = ({
  value,
  onChange,
  placeholder = "Search tracks, artists, albums...",
}) => {
  return (
    <div className="relative flex items-center w-full">
      <Search className="absolute left-3.5 w-4 h-4 text-content-muted pointer-events-none" />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full pl-10 pr-10 py-2.5 rounded-xl bg-surface-hover/80 border border-border/80 text-sm text-content-primary placeholder:text-content-muted focus:outline-none focus:border-primary/80 focus:ring-1 focus:ring-primary/80 transition-all"
      />
      {value && (
        <button
          onClick={() => onChange("")}
          className="absolute right-3 w-5 h-5 flex items-center justify-center rounded-full bg-white/10 text-content-secondary hover:text-white"
        >
          <X className="w-3 h-3" />
        </button>
      )}
    </div>
  );
};
