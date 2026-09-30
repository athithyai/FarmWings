import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";

const here = (p) => fileURLToPath(new URL(p, import.meta.url));

// base "./" keeps every asset/data URL relative, so the same build works on
// GitHub Pages (/<repo>/), any static host, or a sub-path.
export default defineConfig({
  root: here("."),
  publicDir: here("../../public"),
  base: process.env.FARMWINGS_BASE || "./",
  build: {
    outDir: here("../../dist"),
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
  server: { port: 5173 },
});
