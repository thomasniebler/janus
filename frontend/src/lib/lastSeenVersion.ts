const keyFor = (userId: number) => `janus.lastSeenVersion.${userId}`;

/** Whether `version` is a plain `X.Y.Z`; the backend reports `unknown` when it cannot tell. */
export const isReleaseVersion = (version: string) => /^\d+\.\d+\.\d+$/.test(version);

/** The version whose release notes this user last saw in this browser, or null on a first visit. */
export function readLastSeenVersion(userId: number): string | null {
  try {
    return window.localStorage.getItem(keyFor(userId));
  } catch {
    return null;
  }
}

/** Remembers that this user has seen the notes for `version`; storage may be unavailable. */
export function writeLastSeenVersion(userId: number, version: string): void {
  if (!isReleaseVersion(version)) return;
  try {
    window.localStorage.setItem(keyFor(userId), version);
  } catch {
    // Private windows and blocked storage just show the notes again next time.
  }
}
