/** @type {import('tailwindcss').Config} */
// v0.44.0 quiet-canvas theme — tokens adopted from the TrailMix redesign handoff
// (near-black canvas, olive primary accent, cyan/blue secondary accents).
// Palette scales below (slate/purple/indigo/red) are deliberately remapped so the
// hundreds of existing utility classes resolve to the new design tokens:
//   slate-300..600  → text hierarchy (primary-adjacent → faint)
//   purple-*        → olive-tinted borders/badges (was the old purple brand chrome)
//   indigo-*        → accent-blue informational badges/tiles
//   red-*           → #ff4d4d destructive family
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      borderRadius: {
        // handoff spec: small controls (chips/pills/buttons) sit at 7–9px
        DEFAULT: "7px",
      },
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', "ui-sans-serif", "system-ui", "sans-serif"],
        display: ['"Roboto Slab"', "ui-serif", "Georgia", "serif"],
      },
      colors: {
        brand: {
          DEFAULT: "#a4c639",
          dark: "#7e9a26",
          light: "#b8dc43",
        },
        surface: {
          DEFAULT: "#0c0e11",
          raised: "#12151a",
          overlay: "#171b21",
        },
        accent: {
          cyan: "#39c6b7",
          blue: "#3d8bff",
        },
        slate: {
          300: "#c9cfd9",
          400: "#9aa2b1",
          500: "#6b7280",
          600: "#4b5563",
        },
        purple: {
          200: "#e4f0b8",
          300: "#cde58a",
          500: "#a4c639",
          700: "#5c6e2a",
          900: "#4a5240",
        },
        indigo: {
          300: "#8ab4ff",
          600: "#3d8bff",
          700: "#2f6fd6",
          900: "#1e3a66",
        },
        red: {
          300: "#ff9b9b",
          400: "#ff6b6b",
          600: "#ff4d4d",
          700: "#d63c3c",
          900: "#5c1f1f",
        },
      },
    },
  },
  plugins: [],
};
