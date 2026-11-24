import { beforeEach, describe, expect, it } from 'vitest';
import indexHtml from '../index.html?raw';
import bootstrapSource from '../public/ui-shell-bootstrap.js?raw';
import { UI_VERSIONS, UI_VERSION_HTML_CLASSES, UI_VERSION_STORAGE_KEY, type UIVersion } from './contexts/uiVersion';

const shellClasses = Object.values(UI_VERSION_HTML_CLASSES).filter((name): name is string => Boolean(name));

/**
 * Runs the bootstrap once against this document, with its own `window`: the page's query
 * string and a storage holding `cached` (or throwing, like blocked storage). Passing `window`
 * in keeps the test independent of other files that stub the shared jsdom window.
 */
const runBootstrap = ({ search = '', cached = null as string | null, storageBlocked = false } = {}) => {
  const fakeWindow = {
    location: { search },
    localStorage: {
      getItem: (key: string) => {
        if (storageBlocked) throw new DOMException('blocked', 'SecurityError');
        return key === UI_VERSION_STORAGE_KEY ? cached : null;
      },
    },
  };
  new Function('window', 'document', bootstrapSource)(fakeWindow, document);
};

const appliedShell = (): UIVersion => {
  const classes = document.documentElement.classList;
  const match = UI_VERSIONS.find((version) => {
    const name = UI_VERSION_HTML_CLASSES[version];
    return name !== null && classes.contains(name);
  });
  return match ?? 'v1';
};

describe('UI shell bootstrap (index.html → public/ui-shell-bootstrap.js)', () => {
  beforeEach(() => {
    document.documentElement.className = '';
  });

  // JAN-225: the backend's CSP (`script-src 'self'`) blocks inline scripts, so an
  // inline bootstrap silently never ran and every reload painted without the shell class.
  it('is loaded as a same-origin file from <head>, with no inline script left in index.html', () => {
    const doc = new DOMParser().parseFromString(indexHtml, 'text/html');
    const scripts = Array.from(doc.querySelectorAll('script'));
    expect(scripts.filter((script) => !script.getAttribute('src'))).toEqual([]);

    const bootstrap = doc.head.querySelector('script[src="/ui-shell-bootstrap.js"]');
    expect(bootstrap).not.toBeNull();
    // Blocking: it must run before the first paint, not after the bundle.
    expect(bootstrap?.hasAttribute('async')).toBe(false);
    expect(bootstrap?.hasAttribute('defer')).toBe(false);
    expect(bootstrap?.getAttribute('type')).toBeNull();
  });

  it.each(UI_VERSIONS)('applies the cached %s shell class and dark mode', (version) => {
    runBootstrap({ cached: version });
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(appliedShell()).toBe(version);
    const expected = UI_VERSION_HTML_CLASSES[version];
    for (const name of shellClasses) {
      expect(document.documentElement.classList.contains(name)).toBe(name === expected);
    }
  });

  it('lets ?ui= win over the cache and ignores unknown values', () => {
    runBootstrap({ search: '?ui=v2', cached: 'v3' });
    expect(appliedShell()).toBe('v2');

    document.documentElement.className = '';
    runBootstrap({ search: '?ui=v9', cached: 'v3' });
    expect(appliedShell()).toBe('v3');
  });

  it('falls back to the classic shell for an unknown cached value', () => {
    document.documentElement.classList.add('ui-v3');
    runBootstrap({ cached: 'not-a-shell' });
    expect(appliedShell()).toBe('v1');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('keeps the ?ui= escape hatch working when storage is blocked', () => {
    runBootstrap({ search: '?ui=v3', storageBlocked: true });
    expect(appliedShell()).toBe('v3');

    document.documentElement.className = 'ui-v2';
    runBootstrap({ storageBlocked: true });
    expect(appliedShell()).toBe('v1');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('knows exactly the shells uiVersion.ts declares', () => {
    const table = /SHELL_CLASSES\s*=\s*(\{[^}]*\})/.exec(bootstrapSource)?.[1];
    expect(table).toBeDefined();
    expect(new Function(`return ${table}`)()).toEqual(UI_VERSION_HTML_CLASSES);
  });
});
