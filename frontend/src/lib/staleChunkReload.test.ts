import { describe, expect, it, vi } from 'vitest';

import {
  STALE_CHUNK_RELOAD_GUARD_MS,
  installStaleChunkReload,
  isChunkLoadError,
  reloadForStaleChunk,
  withStaleChunkReload,
} from './staleChunkReload';

const memoryStorage = () => {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
  };
};

const staleChunkError = () =>
  new TypeError('Failed to fetch dynamically imported module: https://janus.test/assets/Settings-abc.js');

describe('stale-chunk reload (JAN-179)', () => {
  it('recognizes chunk load failures from every major browser', () => {
    expect(isChunkLoadError(staleChunkError())).toBe(true);
    expect(isChunkLoadError(new TypeError('error loading dynamically imported module: /assets/x.js'))).toBe(true);
    expect(isChunkLoadError(new TypeError('Importing a module script failed.'))).toBe(true);
    expect(isChunkLoadError(new Error('Unable to preload CSS for /assets/x.css'))).toBe(true);
    expect(isChunkLoadError(new Error('Cannot read properties of undefined'))).toBe(false);
    expect(isChunkLoadError(undefined)).toBe(false);
  });

  it('reloads once, then refuses again within the guard window', () => {
    const storage = memoryStorage();
    const reload = vi.fn();

    expect(reloadForStaleChunk({ storage, now: 1_000, reload })).toBe(true);
    expect(reloadForStaleChunk({ storage, now: 1_000 + STALE_CHUNK_RELOAD_GUARD_MS - 1, reload })).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);

    expect(reloadForStaleChunk({ storage, now: 1_000 + STALE_CHUNK_RELOAD_GUARD_MS, reload })).toBe(true);
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it('never reloads when the loop guard cannot be stored', () => {
    const reload = vi.fn();
    const throwingStorage = {
      getItem: () => null,
      setItem: () => {
        throw new Error('QuotaExceededError');
      },
    };

    expect(reloadForStaleChunk({ storage: null, reload })).toBe(false);
    expect(reloadForStaleChunk({ storage: throwingStorage, reload })).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });

  it('turns a stale-chunk import failure into a reload that never settles', async () => {
    const reload = vi.fn();
    const load = withStaleChunkReload(() => Promise.reject(staleChunkError()), {
      storage: memoryStorage(),
      reload,
    });

    const outcome = await Promise.race([
      load().then(
        () => 'resolved',
        () => 'rejected'
      ),
      new Promise((resolve) => setTimeout(() => resolve('pending'), 20)),
    ]);

    expect(outcome).toBe('pending');
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('rethrows other errors, and a stale chunk once the guard has fired', async () => {
    const storage = memoryStorage();
    const reload = vi.fn();
    const boom = new Error('boom');

    await expect(withStaleChunkReload(() => Promise.reject(boom), { storage, reload })()).rejects.toBe(boom);

    storage.setItem('janus_stale_chunk_reload_at', String(Date.now()));
    const stale = staleChunkError();
    await expect(
      withStaleChunkReload(() => Promise.reject(stale), { storage, reload, now: Date.now() })()
    ).rejects.toBe(stale);
    expect(reload).not.toHaveBeenCalled();
  });

  it('handles vite:preloadError by preventing the rethrow only when it reloads', () => {
    const target = new EventTarget() as unknown as Window;
    installStaleChunkReload(target);
    const event = new Event('vite:preloadError', { cancelable: true });

    // jsdom cannot navigate, so stub the reload the default path would trigger.
    const reload = vi.fn();
    const original = window.location;
    Object.defineProperty(window, 'location', { configurable: true, value: { ...original, reload } });
    try {
      window.sessionStorage.removeItem('janus_stale_chunk_reload_at');
      target.dispatchEvent(event);
      expect(reload).toHaveBeenCalledTimes(1);
      expect(event.defaultPrevented).toBe(true);

      const second = new Event('vite:preloadError', { cancelable: true });
      target.dispatchEvent(second);
      expect(reload).toHaveBeenCalledTimes(1);
      expect(second.defaultPrevented).toBe(false);
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: original });
      window.sessionStorage.removeItem('janus_stale_chunk_reload_at');
    }
  });
});
