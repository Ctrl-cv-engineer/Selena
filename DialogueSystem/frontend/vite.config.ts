import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";
import { traeBadgePlugin } from "vite-plugin-trae-solo-badge";

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(currentDir, "..", "..");
const configPath = path.resolve(projectRoot, "config.json");

function readFrontendConfig() {
  try {
    const raw = fs.readFileSync(configPath, "utf-8");
    const parsed = JSON.parse(raw);
    const frontend = parsed.Frontend ?? {};
    return {
      host: frontend.host ?? "127.0.0.1",
      port: Number(frontend.port ?? 5173),
      apiPort: Number(frontend.api_port ?? 8000),
    };
  } catch {
    return {
      host: "127.0.0.1",
      port: 5173,
      apiPort: 8000,
    };
  }
}

const frontendConfig = readFrontendConfig();

export default defineConfig({
  root: currentDir,
  resolve: {
    alias: {
      "@": path.resolve(currentDir, "src"),
    },
  },
  build: {
    sourcemap: "hidden",
  },
  server: {
    host: frontendConfig.host,
    port: frontendConfig.port,
    strictPort: true,
    proxy: {
      "/api": {
        target: `http://${frontendConfig.host}:${frontendConfig.apiPort}`,
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: frontendConfig.host,
    port: frontendConfig.port,
    strictPort: true,
  },
  plugins: [
    react({
      babel: {
        plugins: ["react-dev-locator"],
      },
    }),
    traeBadgePlugin({
      variant: "dark",
      position: "bottom-right",
      prodOnly: true,
      clickable: true,
      clickUrl: "https://www.trae.ai/solo?showJoin=1",
      autoTheme: true,
      autoThemeTarget: "#root",
    }),
    tsconfigPaths({
      projects: [path.resolve(currentDir, "tsconfig.json")],
    }),
  ],
});
