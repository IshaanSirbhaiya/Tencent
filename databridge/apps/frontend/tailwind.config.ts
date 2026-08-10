import type { Config } from "tailwindcss";

// Design law (D-FE-3): near-neutral gray scale + ONE accent (#2563EB). The 3-stop confidence
// scale is the only other semantic colour. No rainbow palettes, no gradients.
const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "hsl(var(--bg))",
        surface: "hsl(var(--surface))",
        elevated: "hsl(var(--elevated))",
        border: "hsl(var(--border))",
        muted: "hsl(var(--muted))",
        fg: "hsl(var(--fg))",
        subtle: "hsl(var(--subtle))",
        accent: "#2563EB",
        "accent-fg": "#ffffff",
        // single 3-stop confidence scale (auto / spot_check / review)
        "band-auto": "#16a34a",
        "band-spot": "#d97706",
        "band-review": "#dc2626",
        // table roles (muted, not neon)
        "role-entity": "#2563EB",
        "role-event": "#d97706",
        "role-log": "#6b7280",
        "role-relationship": "#7c3aed",
        "role-lookup": "#16a34a",
        // Company-Brain node types (T08). Reuse the role scale; project adds one teal token.
        // Disciplined, token-derived — NOT a rainbow category palette (D-FE-3).
        "node-person": "#2563EB",
        "node-org": "#7c3aed",
        "node-decision": "#d97706",
        "node-project": "#0d9488",
        "node-fact": "#16a34a",
        "node-document": "#6b7280",
      },
      keyframes: {
        // subtle, meaningful motion only (~120–200ms; skill design law). No decorative bounce.
        "count-in": { from: { opacity: "0", transform: "translateY(3px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        "node-in": { from: { opacity: "0", transform: "scale(0.6)" }, to: { opacity: "1", transform: "scale(1)" } },
        "pulse-ring": { "0%": { boxShadow: "0 0 0 0 rgba(37,99,235,0.55)" }, "70%": { boxShadow: "0 0 0 12px rgba(37,99,235,0)" }, "100%": { boxShadow: "0 0 0 0 rgba(37,99,235,0)" } },
      },
      animation: {
        "count-in": "count-in 180ms ease-out",
        "node-in": "node-in 220ms ease-out both",
        "pulse-ring": "pulse-ring 1.1s ease-out 2",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "Arial", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      borderRadius: { DEFAULT: "6px" },
    },
  },
  plugins: [],
};

export default config;
