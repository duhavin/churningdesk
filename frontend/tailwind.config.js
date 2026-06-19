/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Alphacore-ish palette: cyan/pink accents on near-black.
        ink: {
          900: "#06080d",
          800: "#0a0e16",
          700: "#10151f",
          600: "#161c29",
          500: "#1e2636",
          400: "#2a3447",
        },
        cyan: { accent: "#22d3ee" },
        pink: { accent: "#f472b6" },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
