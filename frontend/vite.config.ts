import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            // The app shells and pages are lazy-loaded chunks (JAN-179). Without this group
            // every lucide icon shared between two of them becomes its own sub-1 kB chunk;
            // one shared icon chunk keeps the request count sane.
            { name: 'icons', test: /node_modules[\\/]lucide-react/ },
          ],
        },
      },
    },
  },
})
