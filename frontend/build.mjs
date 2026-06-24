import react from "@vitejs/plugin-react";
import { build } from "vite";

await build({
  configFile: false,
  root: process.cwd(),
  plugins: [react()],
  build: {
    outDir: "dist",
  },
});
