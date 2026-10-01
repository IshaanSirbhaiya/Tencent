import type { Config } from "tailwindcss";

// Design law (D-FE-3): near-neutral gray scale + ONE accent. The 3-stop confidence scale is the
// only other semantic colour. No rainbow palettes, no gradients.
//
// Semantic colours are CSS variables, defined per theme in `app/globals.css`, because their JOB is
// to stay distinguishable — and the hex that reads well on a near-black ground does not read well
// on a white one. A literal here would look fine in one theme and fail in the other.
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
        // `<alpha-value>` so `bg-band-review/10` and `border-accent/40` keep working: without it
        // Tailwind cannot apply an opacity modifier to a var-backed colour and every tint would
        // render fully opaque.
        accent: "hsl(var(--accent) / <alpha-value>)",
        "accent-fg": "hsl(var(--accent-fg) / <alpha-value>)",
        // single 3-stop confidence scale (auto / spot_check / review)
        "band-auto": "hsl(var(--band-auto) / <alpha-value>)",
        "band-spot": "hsl(var(--band-spot) / <alpha-value>)",
        "band-review": "hsl(var(--band-review) / <alpha-value>)",
        // table roles (muted, not neon)
        "role-entity": "hsl(var(--role-entity) / <alpha-value>)",
        "role-event": "hsl(var(--role-event) / <alpha-value>)",
        "role-log": "hsl(var(--role-log) / <alpha-value>)",
        "role-relationship": "hsl(var(--role-relationship) / <alpha-value>)",
        "role-lookup": "hsl(var(--role-lookup) / <alpha-value>)",
        // `/graph` canvas — one fixed dark palette, not theme-swapped (see globals.css note by the
        // token definitions).
        "graph-bg": "hsl(var(--graph-bg) / <alpha-value>)",
        "graph-surface": "hsl(var(--graph-surface) / <alpha-value>)",
        "graph-elevated": "hsl(var(--graph-elevated) / <alpha-value>)",
        "graph-border": "hsl(var(--graph-border) / <alpha-value>)",
        "graph-muted": "hsl(var(--graph-muted) / <alpha-value>)",
        "graph-fg": "hsl(var(--graph-fg) / <alpha-value>)",
        "graph-subtle": "hsl(var(--graph-subtle) / <alpha-value>)",
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
