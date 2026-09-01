import { svelte } from '@sveltejs/vite-plugin-svelte'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'
import { resolve } from 'node:path'

// Builds straight into the FastAPI app's static mount (see oko/api/app.py's
// StaticFiles(directory=STATIC_DIR)) so `npm run build` output is served
// with no extra copy step in local dev; the Docker image instead copies
// this same output from a dedicated frontend-builder stage.
const STATIC_DIR = resolve(import.meta.dirname, '../src/oko/api/static')

export default defineConfig({
  plugins: [tailwindcss(), svelte()],
  resolve: {
    alias: {
      $lib: resolve(import.meta.dirname, './src/lib'),
    },
  },
  build: {
    outDir: STATIC_DIR,
    emptyOutDir: false,
  },
  server: {
    proxy: {
      '/de.json': 'http://localhost:8000',
      '/exchanges.json': 'http://localhost:8000',
      '/zones.geojson': 'http://localhost:8000',
      '/zones': 'http://localhost:8000',
      '/history': 'http://localhost:8000',
      '/api/': 'http://localhost:8000',
      '/healthz': 'http://localhost:8000',
      // Every other public zone's forecast, e.g. /FR.json, /DK-DK1.json --
      // narrow regex so it doesn't also swallow /, /@vite/*, /src/*, etc.
      '^/[A-Z]{2}(-[A-Z0-9]+)?\\.json$': 'http://localhost:8000',
    },
  },
})
