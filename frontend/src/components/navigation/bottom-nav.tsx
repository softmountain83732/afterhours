"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Disc3, Radio, ListMusic, User } from "lucide-react";
import { useTelegram } from "@/contexts/telegram-context";

export const BottomNav: React.FC = () => {
  const pathname = usePathname();
  const { haptic } = useTelegram();

  const navItems = [
    { name: "Library", href: "/library", icon: Disc3 },
    { name: "Room", href: "/room", icon: Radio },
    { name: "Playlist", href: "/playlist", icon: ListMusic },
    { name: "Profile", href: "/profile", icon: User },
  ];

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-40 bg-surface/90 backdrop-blur-md border-t border-border/80 px-4 pb-[max(env(safe-area-inset-bottom),8px)] pt-2">
      <div className="max-w-md mx-auto flex items-center justify-around">
        {navItems.map((item) => {
          const isActive = pathname.startsWith(item.href);
          const Icon = item.icon;

          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={() => haptic("light")}
              className={`flex flex-col items-center justify-center py-1 px-3 transition-colors ${
                isActive
                  ? "text-primary"
                  : "text-content-secondary hover:text-content-primary"
              }`}
            >
              <Icon
                className={`w-5 h-5 mb-1 transition-transform ${
                  isActive ? "scale-110 stroke-[2.25]" : "stroke-[1.75]"
                }`}
              />
              <span className="text-[11px] font-medium tracking-tight">
                {item.name}
              </span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
};
