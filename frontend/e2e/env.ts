import { createHash } from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Shared, isolated runtime configuration for the Playwright backend used by e2e tests.
 *
 * A fresh temp directory (DB + XDG dirs) per run keeps e2e state fully separate from any
 * real local Janus instance. The directory and the default port are derived from the
 * checkout's path (JAN-291), so runs from different checkouts or worktrees on one machine
 * neither wipe each other's database nor fight over the backend port.
 */
const CHECKOUT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const CHECKOUT_HASH = createHash('sha256').update(CHECKOUT_ROOT).digest('hex');

// 8100-8899: clear of the dev backend (8000) and the Vite dev server (5173). Two
// checkouts can still hash to the same port; set E2E_PORT to pick another.
const DEFAULT_E2E_PORT = 8100 + (parseInt(CHECKOUT_HASH.slice(0, 8), 16) % 800);

export const E2E_HOST = '127.0.0.1';
export const E2E_PORT = Number(process.env.E2E_PORT || DEFAULT_E2E_PORT);
export const E2E_BASE_URL = `http://${E2E_HOST}:${E2E_PORT}`;

export const E2E_GOOGLE_CLIENT_ID = 'e2e-client.apps.googleusercontent.com';
export const E2E_GOOGLE_CLIENT_SECRET = 'e2e-google-client-secret-never-returned';

// The local fake Google and Jira (`fake-upstream.ts`, JAN-221). The backend reads the fake
// Google's URL once at start (JANUS_GOOGLE_API_BASE_URL below), so its port is fixed per
// checkout: 1000 above the backend port unless E2E_FAKE_UPSTREAM_PORT is set.
export const E2E_FAKE_UPSTREAM_PORT = Number(process.env.E2E_FAKE_UPSTREAM_PORT || E2E_PORT + 1000);
export const E2E_FAKE_UPSTREAM_URL = `http://${E2E_HOST}:${E2E_FAKE_UPSTREAM_PORT}`;

export const E2E_ADMIN_USERNAME = 'e2e-admin';
export const E2E_ADMIN_PASSWORD = 'e2e-test-password-1234!';

// A stable (not per-process) path: Playwright re-imports this module in the main runner
// process, in globalSetup, and in every worker process, so a value derived from
// `process.pid` would disagree between them. The checkout hash is the same in all of
// them. E2E_DATA_DIR overrides it; so does TMPDIR, through os.tmpdir(). Two runs from
// the same checkout at once still share the directory and are not supported.
export const E2E_DATA_DIR =
  process.env.E2E_DATA_DIR || path.join(os.tmpdir(), `janus-e2e-${CHECKOUT_HASH.slice(0, 12)}`);
export const E2E_STORAGE_STATE_PATH = path.join(E2E_DATA_DIR, 'storage-state.json');

export const E2E_XDG_DATA_HOME = path.join(E2E_DATA_DIR, 'xdg-data');
export const E2E_XDG_STATE_HOME = path.join(E2E_DATA_DIR, 'xdg-state');
export const E2E_XDG_CONFIG_HOME = path.join(E2E_DATA_DIR, 'xdg-config');
export const E2E_DATABASE_URL = `sqlite:///${path.join(E2E_DATA_DIR, 'janus-e2e.db')}`;

// The e2e backend imports `neonize` from `fake-neonize/` instead of the real library
// (JAN-223), so WhatsApp specs script pairing and messages and nothing ever reaches
// WhatsApp. Specs talk to the fake client through files in this directory.
export const E2E_FAKE_NEONIZE_PATH = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fake-neonize');
export const E2E_FAKE_NEONIZE_DIR = path.join(E2E_DATA_DIR, 'fake-neonize');

export const E2E_BACKEND_ENV: Record<string, string> = {
  DATABASE_URL: E2E_DATABASE_URL,
  JANUS_SECRET_KEY: 'e2e-test-secret-key-for-playwright-only-32ch',
  JANUS_BASE_URL: E2E_BASE_URL,
  // The backend serves the built frontend from its own origin here (rather than through
  // the Vite dev proxy), so the `/ws/briefing` and `/ws/chat` sockets' Origin check needs
  // this origin allow-listed too -- otherwise every websocket handshake 403s regardless of
  // whether the feature actually works. See briefing.spec.ts.
  ALLOWED_ORIGINS: E2E_BASE_URL,
  JANUS_RUNTIME_TRIGGER_WORKER_ENABLED: 'false',
  // The backend's own Google calls (token exchange, userinfo, Calendar, Tasks) go to the local
  // fake instead of Google; oauthlib refuses a plain-http token endpoint without the second flag.
  // The Google OAuth client now comes from the configuration, not the database (settings.toml in real installs).
  JANUS_GOOGLE_CLIENT_ID: E2E_GOOGLE_CLIENT_ID,
  JANUS_GOOGLE_CLIENT_SECRET: E2E_GOOGLE_CLIENT_SECRET,
  JANUS_GOOGLE_API_BASE_URL: `${E2E_FAKE_UPSTREAM_URL}/google`,
  // The Telegram Bot API goes to the same fixed-port fake (JAN-405, telegram.spec.ts).
  JANUS_TELEGRAM_API_BASE_URL: `${E2E_FAKE_UPSTREAM_URL}/telegram`,
  OAUTHLIB_INSECURE_TRANSPORT: '1',
  // Azure OpenAI endpoints must be https; this test seam admits the plain-http loopback fake (JAN-349).
  JANUS_AZURE_OPENAI_ALLOW_LOOPBACK_HTTP: 'true',
  XDG_DATA_HOME: E2E_XDG_DATA_HOME,
  XDG_STATE_HOME: E2E_XDG_STATE_HOME,
  XDG_CONFIG_HOME: E2E_XDG_CONFIG_HOME,
  PYTHONPATH: [E2E_FAKE_NEONIZE_PATH, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
  JANUS_E2E_FAKE_NEONIZE_DIR: E2E_FAKE_NEONIZE_DIR,
};
