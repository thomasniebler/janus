import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { installStaleChunkReload } from './lib/staleChunkReload'

// Reload into a new build when a deploy removed a lazy chunk's dependencies (JAN-179).
installStaleChunkReload()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
