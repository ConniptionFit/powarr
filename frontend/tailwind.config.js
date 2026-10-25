/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: "#7c3aed",
          dark: "#5b21b6",
          light: "#a78bfa",
        },
        // Channels come from CSS variables (index.css) so the light theme can swap them.
        surface: {
          DEFAULT: "rgb(var(--c-surface) / <alpha-value>)",
          raised: "rgb(var(--c-surface-raised) / <alpha-value>)",
          overlay: "rgb(var(--c-surface-overlay) / <alpha-value>)",
        },
      },
    },
  },
  plugins: [],
};
