import react from "@vitejs/plugin-react";
import { build } from "vite";

await build({
  configFile: false,
  root: process.cwd(),
  // Subpath hosting (e.g. /wewards/ behind the tailnet proxy); defaults to "/"
  // so local serve-dist.mjs deploys are unchanged.
  base: process.env.VITE_BASE_PATH || "/",
  plugins: [react()],
  build: {
    outDir: "dist",
  },
});
