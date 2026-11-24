import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { defineConfig, devices } from '@playwright/test';
import { E2E_BACKEND_ENV, E2E_BASE_URL, E2E_DATA_DIR, E2E_HOST, E2E_PORT, E2E_STORAGE_STATE_PATH } from './e2e/env';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// This config module is re-evaluated multiple times per run: once by the main
// Playwright CLI process (before the webServer or any worker starts), and again by
// each worker process it spawns (which inherit the main process's `process.env` at
// spawn time). Wiping the data dir unconditionally here would also fire from those
// later worker re-imports, deleting the DB out from under the webServer that, by
// then, has already started against it. The env-var guard below ensures the wipe
// happens exactly once, in the first (main-process) evaluation, before the
// webServer's DATABASE_URL is ever opened — so every run starts from a clean,
// unmigrated database without corrupting an already-running one. Without this,
// a second run would otherwise reuse the previous run's DB (already has an admin
// user), and global-setup's `bootstrap-admin` step would fail with "Initial setup
// has already been completed."
// The wipe only touches a directory an earlier e2e run created (it carries the
// marker file) or an empty/missing one, so a mistyped E2E_DATA_DIR pointing at
// real data fails loudly instead of being deleted.
const E2E_DATA_DIR_MARKER = path.join(E2E_DATA_DIR, '.janus-e2e-data-dir');
if (!process.env.JANUS_E2E_DATA_DIR_PREPARED) {
  if (fs.existsSync(E2E_DATA_DIR) && fs.readdirSync(E2E_DATA_DIR).length > 0 && !fs.existsSync(E2E_DATA_DIR_MARKER)) {
    throw new Error(`Refusing to wipe ${E2E_DATA_DIR}: it is not empty and was not created by an e2e run.`);
  }
  fs.rmSync(E2E_DATA_DIR, { recursive: true, force: true });
  fs.mkdirSync(E2E_DATA_DIR, { recursive: true });
  fs.writeFileSync(E2E_DATA_DIR_MARKER, '');
  process.env.JANUS_E2E_DATA_DIR_PREPARED = '1';
}

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  // On CI each shard also writes a blob report; the `e2e-report` job merges them (JAN-345).
  reporter: process.env.CI ? [['github'], ['blob']] : 'list',
  globalSetup: './e2e/global-setup.ts',
  use: {
    baseURL: E2E_BASE_URL,
    storageState: E2E_STORAGE_STATE_PATH,
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    // The optional `whatsapp` extra brings segno, which draws the pairing QR code; `neonize`
    // itself comes from the fake on PYTHONPATH (E2E_BACKEND_ENV), which shadows the real one.
    command: `uv run --extra whatsapp janus start --host ${E2E_HOST} --port ${E2E_PORT}`,
    cwd: path.resolve(__dirname, '..'),
    url: `${E2E_BASE_URL}/api/status/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: E2E_BACKEND_ENV,
  },
});
