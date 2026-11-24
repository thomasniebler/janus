/**
 * Stale-chunk recovery (JAN-179).
 *
 * The shells and pages are lazy-loaded, hashed chunks. A deploy replaces the asset tree
 * (`rsync --delete`), so a tab left open across a deploy asks for chunk URLs that no longer
 * exist. Instead of letting the root error boundary unmount the app, a failed chunk load
 * triggers one full reload into the new build. A sessionStorage timestamp guards against a
 * reload loop: a second failure within the guard window surfaces as a normal error.
 */

const RELOAD_STORAGE_KEY = 'janus_stale_chunk_reload_at';

/** A failure inside this window after the last automatic reload is not retried. */
export const STALE_CHUNK_RELOAD_GUARD_MS = 30_000;

// Chromium, Firefox and Safari wording for a dynamic import whose module could not be loaded,
// plus Vite's own preload helper failures.
const CHUNK_LOAD_ERROR_PATTERN =
  /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Unable to preload CSS|Failed to load module script/i;

export const isChunkLoadError = (error: unknown): boolean => {
  const message = error instanceof Error ? error.message : typeof error === 'string' ? error : '';
  return CHUNK_LOAD_ERROR_PATTERN.test(message);
};

interface ReloadDeps {
  storage?: Pick<Storage, 'getItem' | 'setItem'> | null;
  now?: number;
  reload?: () => void;
}

const sessionStorageOrNull = (): Storage | null => {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
};

/**
 * Reloads the page once to pick up a new build. Returns `false` (and does nothing) when an
 * automatic reload already happened within the guard window, or when sessionStorage is
 * unavailable and the loop guard therefore cannot work.
 */
export const reloadForStaleChunk = ({
  storage = sessionStorageOrNull(),
  now = Date.now(),
  reload = () => window.location.reload(),
}: ReloadDeps = {}): boolean => {
  if (!storage) {
    return false;
  }
  try {
    const last = Number(storage.getItem(RELOAD_STORAGE_KEY));
    if (Number.isFinite(last) && last > 0 && now - last < STALE_CHUNK_RELOAD_GUARD_MS) {
      return false;
    }
    storage.setItem(RELOAD_STORAGE_KEY, String(now));
  } catch {
    return false;
  }
  reload();
  return true;
};

/**
 * Wraps a dynamic import so a stale-chunk failure reloads into the new build. While the
 * reload is underway the returned promise never settles, so Suspense keeps its fallback
 * instead of the error boundary unmounting the app.
 */
export const withStaleChunkReload =
  <T>(factory: () => Promise<T>, deps?: ReloadDeps): (() => Promise<T>) =>
  () =>
    factory().catch((error: unknown) => {
      if (isChunkLoadError(error) && reloadForStaleChunk(deps)) {
        return new Promise<T>(() => undefined);
      }
      throw error;
    });

/**
 * Handles Vite's `vite:preloadError` (a chunk's preloaded dependency failed) the same way.
 * `preventDefault()` stops Vite from rethrowing while the page reloads.
 */
export const installStaleChunkReload = (target: Window = window): void => {
  target.addEventListener('vite:preloadError', (event) => {
    if (reloadForStaleChunk()) {
      event.preventDefault();
    }
  });
};
