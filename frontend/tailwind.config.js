/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Deep-space terminal background, layered for elevation.
        bg: "#070b14",
        panel: "#0d1424",
        panelhi: "#16203a",
        edge: "#1e2a4a",
        // Semantic market colors (kept vivid for scanning).
        bull: "#16c784",
        bear: "#ea3943",
        neutral: "#8b95a7",
        // Premium gold accent — intelligence/institutional, not generic SaaS blue.
        accent: "#e8c468",
        accenthi: "#f5dd9a",
        // Interactive blue for in-app selection/CTA affordances (dashboard).
        blue: "#3b82f6",
        bluehi: "#60a5fa",
        ink: "#eef1f7",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      letterSpacing: {
        tightest: "-0.03em",
      },
      boxShadow: {
        // Subtle, premium elevation — glow rather than drop shadow.
        card: "0 1px 0 0 rgba(255,255,255,0.04) inset, 0 8px 30px rgba(0,0,0,0.35)",
        glow: "0 0 0 1px rgba(232,196,104,0.18), 0 8px 40px rgba(232,196,104,0.07)",
      },
      borderRadius: {
        xl2: "1.25rem",
      },
    },
  },
  plugins: [],
};
