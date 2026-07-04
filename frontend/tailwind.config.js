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
        surface: {
          DEFAULT: "#1a1a2e",
          raised: "#16213e",
          overlay: "#0f3460",
        },
      },
    },
  },
  plugins: [],
};
