import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { request } from '@playwright/test';
import {
  E2E_ADMIN_PASSWORD,
  E2E_ADMIN_USERNAME,
  E2E_BACKEND_ENV,
  E2E_BASE_URL,
  E2E_DATA_DIR,
  E2E_STORAGE_STATE_PATH,
} from './env';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..', '..');

/**
 * Provisions a throwaway admin user directly via the `janus user bootstrap-admin` CLI
 * (bypassing the one-time setup-token flow, which is only reachable through server logs)
 * and stores a logged-in browser storage state that every spec reuses by default.
 */
export default async function globalSetup(): Promise<void> {
  fs.mkdirSync(E2E_DATA_DIR, { recursive: true });

  execFileSync(
    'uv',
    ['run', 'janus', 'user', 'bootstrap-admin', E2E_ADMIN_USERNAME, '--password', E2E_ADMIN_PASSWORD],
    {
      cwd: REPO_ROOT,
      env: { ...process.env, ...E2E_BACKEND_ENV },
      stdio: 'inherit',
    }
  );

  const apiContext = await request.newContext({ baseURL: E2E_BASE_URL });
  const response = await apiContext.post('/api/auth/login', {
    data: { username: E2E_ADMIN_USERNAME, password: E2E_ADMIN_PASSWORD },
  });
  if (!response.ok()) {
    throw new Error(`e2e login failed during global setup: ${response.status()} ${await response.text()}`);
  }
  await apiContext.storageState({ path: E2E_STORAGE_STATE_PATH });
  await apiContext.dispose();
}
