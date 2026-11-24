import { spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { E2E_BACKEND_ENV, E2E_DATA_DIR, E2E_HOST } from './env';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

// `auth_service.ensure_setup_token` logs the token at startup in exactly this shape.
const SETUP_TOKEN_PATTERN = /Use this one-time token within \d+ minutes: (\S+)/;

export interface FreshBackend {
  baseURL: string;
  /** The one-time setup token the backend printed at startup, as an operator would copy it. */
  setupToken: string;
  stop: () => Promise<void>;
}

const freePort = (): Promise<number> =>
  new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, E2E_HOST, () => {
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : 0;
      server.close(() => resolve(port));
    });
  });

const stopProcess = (child: ChildProcess): Promise<void> =>
  new Promise((resolve) => {
    if (child.exitCode !== null || child.signalCode !== null || child.pid === undefined) {
      resolve();
      return;
    }
    const killTimer = setTimeout(() => {
      try {
        process.kill(-child.pid!, 'SIGKILL');
      } catch {
        // already gone
      }
    }, 10_000);
    child.once('exit', () => {
      clearTimeout(killTimer);
      resolve();
    });
    try {
      // The backend runs in its own process group (`detached`), so this reaches both
      // `uv` and the Python server it started.
      process.kill(-child.pid, 'SIGTERM');
    } catch {
      clearTimeout(killTimer);
      resolve();
    }
  });

/**
 * Starts a second, user-less backend with public first-run setup enabled (JAN-218).
 *
 * The shared e2e backend already has an admin (global-setup bootstraps one) and keeps
 * public setup off, so the real token -> admin -> login flow needs its own process:
 * its own database, XDG dirs (and with them its own instance lock) and a free port. The
 * setup token is read from the backend's startup log, exactly where an operator finds it.
 * It serves the same built frontend as the main e2e backend.
 */
export const startFreshBackend = async (name: string): Promise<FreshBackend> => {
  const dataDir = path.join(E2E_DATA_DIR, `fresh-${name}`);
  fs.rmSync(dataDir, { recursive: true, force: true });
  fs.mkdirSync(dataDir, { recursive: true });

  const port = await freePort();
  const baseURL = `http://${E2E_HOST}:${port}`;
  const env: Record<string, string> = {
    ...(process.env as Record<string, string>),
    ...E2E_BACKEND_ENV,
    DATABASE_URL: `sqlite:///${path.join(dataDir, 'janus-e2e.db')}`,
    JANUS_BASE_URL: baseURL,
    ALLOWED_ORIGINS: baseURL,
    JANUS_PUBLIC_SETUP_ENABLED: 'true',
    XDG_DATA_HOME: path.join(dataDir, 'xdg-data'),
    XDG_STATE_HOME: path.join(dataDir, 'xdg-state'),
    XDG_CONFIG_HOME: path.join(dataDir, 'xdg-config'),
  };

  const child = spawn('uv', ['run', 'janus', 'start', '--host', E2E_HOST, '--port', String(port)], {
    cwd: REPO_ROOT,
    env,
    detached: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  let output = '';
  const setupToken = await new Promise<string>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`fresh backend printed no setup token within 60s:\n${output}`));
    }, 60_000);
    const onData = (chunk: Buffer) => {
      output += chunk.toString();
      const match = SETUP_TOKEN_PATTERN.exec(output);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    };
    child.stdout?.on('data', onData);
    child.stderr?.on('data', onData);
    child.once('exit', (code) => {
      clearTimeout(timer);
      reject(new Error(`fresh backend exited with ${code} before printing a setup token:\n${output}`));
    });
  }).catch(async (error) => {
    await stopProcess(child);
    throw error;
  });

  // The token is logged during startup, before uvicorn accepts connections.
  const deadline = Date.now() + 60_000;
  for (;;) {
    try {
      const response = await fetch(`${baseURL}/api/status/health`);
      if (response.ok) break;
    } catch {
      // not listening yet
    }
    if (Date.now() > deadline) {
      await stopProcess(child);
      throw new Error(`fresh backend never became healthy:\n${output}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }

  return {
    baseURL,
    setupToken,
    stop: async () => {
      await stopProcess(child);
      fs.rmSync(dataDir, { recursive: true, force: true });
    },
  };
};
