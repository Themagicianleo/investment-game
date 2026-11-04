/// <reference types="vitest" />
import { defineConfig } from 'vite';

export default defineConfig({
  // BASE_PATH is set by the GitHub Pages workflow (e.g. "/investment-game/").
  base: process.env.BASE_PATH ?? '/',
  server: { host: true },
  preview: { host: true },
  test: { environment: 'node' },
});
