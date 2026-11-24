import { describe, expect, it, vi } from 'vitest';

// Stand-ins for the real shells: with `isolate: false` each real shell module may only be
// imported by its own test file (see src/test/shellChatPlacement.tsx).
vi.mock('./layout/Layout', () => ({ default: function MockLayout() { return null; } }));
vi.mock('./layout-v2/LayoutV2', () => ({ default: function MockLayoutV2() { return null; } }));
vi.mock('./layout-v3/LayoutV3', () => ({ default: function MockLayoutV3() { return null; } }));

import { getLoadedAppShell, loadAppShell } from './appShells';

describe('app shell registry (JAN-179)', () => {
  it('loads each shell on demand and caches it for synchronous rendering', async () => {
    expect(getLoadedAppShell('v2')).toBeUndefined();

    const shell = await loadAppShell('v2');

    expect(shell.name).toBe('MockLayoutV2');
    expect(getLoadedAppShell('v2')).toBe(shell);
    await expect(loadAppShell('v2')).resolves.toBe(shell);
  });

  it('keeps the shells separate per UI version', async () => {
    const [v1, v3] = await Promise.all([loadAppShell('v1'), loadAppShell('v3')]);

    expect(v1.name).toBe('MockLayout');
    expect(v3.name).toBe('MockLayoutV3');
    expect(getLoadedAppShell('v1')).toBe(v1);
    expect(getLoadedAppShell('v3')).toBe(v3);
  });
});
