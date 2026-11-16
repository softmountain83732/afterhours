import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "#08080c",
        surface: {
          DEFAULT: "#121218",
          hover: "#1a1a24",
          light: "#20202c",
        },
        border: {
          DEFAULT: "#232332",
          subtle: "#191924",
        },
        primary: {
          DEFAULT: "#7c3aed",
          hover: "#6d28d9",
          muted: "#4c1d95",
        },
        accent: {
          DEFAULT: "#3b82f6",
          hover: "#2563eb",
          glow: "rgba(59, 130, 246, 0.2)",
        },
        content: {
          primary: "#f1f1f5",
          secondary: "#8a8a9e",
          muted: "#55556a",
        },
      },
      fontFamily: {
        sans: ["var(--font-geist-sans)", "system-ui", "-apple-system", "sans-serif"],
        mono: ["var(--font-geist-mono)", "monospace"],
      },
      animation: {
        "pulse-slow": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "fade-in": "fadeIn 0.25s ease-out forwards",
        "slide-up": "slideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards",
      },
      keyframes: {
        fadeIn: {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        slideUp: {
          "0%": { transform: "translateY(100%)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
      },
    },
  },
  plugins: [],
};
export default config;
