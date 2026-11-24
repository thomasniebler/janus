import { vi } from 'vitest';

/**
 * A single, shared `vi.mock('../../api', ...)` factory body for every test file that mocks
 * the API client module.
 *
 * With `isolate: false` (see vitest.config.ts), the module registry is shared across
 * test files within a worker: whichever file's `vi.mock('../../api', factory)` call
 * runs first "wins" that registration for every other file executed in the same
 * worker afterwards. A factory that only stubs the one or two namespaces a given file
 * cares about (e.g. `{ mailIngestionApi: { getSettings: vi.fn() } }`) therefore left
 * every *other* namespace `undefined` for whichever file happened to run later in the
 * same worker -- surfacing as flaky, order-dependent failures where a component's
 * query function throws on an undefined API call and the component gets stuck
 * rendering its loading state forever.
 *
 * Using this exact same factory body everywhere means whichever file's registration
 * wins, the resulting mocked module always has the complete real API surface (every
 * namespace, every method) replaced with a fresh `vi.fn()` -- so every test file's
 * `vi.mocked(someApi.someMethod).mockResolvedValue(...)` calls keep working
 * regardless of file execution order.
 *
 * Callers must invoke this from inside a dynamic `import()` within their own
 * `vi.mock('../../api', async (importOriginal) => { ... })` factory, not as a
 * statically imported function reference passed directly to `vi.mock` -- Vitest
 * hoists `vi.mock` calls above the rest of the file (including other imports), so a
 * statically imported helper referenced directly in the factory throws
 * "Cannot access '<binding>' before initialization". A dynamic import inside the
 * factory body defers resolution until the factory actually runs, after hoisting:
 *
 *   vi.mock('../../api', async (importOriginal) => {
 *     const { mockApiModule } = await import('../../test/apiMock');
 *     return mockApiModule(importOriginal);
 *   });
 */
/**
 * One `vi.fn()` per `namespace.method` for the whole worker. Vitest re-runs the factory
 * for every test file that calls `vi.mock`, but a component module another file already
 * loaded stays bound to the earlier mocked module; with fresh functions per run, that
 * component would call mocks the current file never configures (seen with LLMConfig.test
 * loading LLMModelSelect.tsx before LLMModelSelect.test ran in the same worker). Reusing
 * the functions keeps every instance of the mocked module on the same mocks; each test
 * still sets its own implementation and clears call history at its start.
 */
const sharedMocks: Map<string, ReturnType<typeof vi.fn>> = ((globalThis as Record<string, unknown>).__janusApiMocks ??=
  new Map()) as Map<string, ReturnType<typeof vi.fn>>;

const sharedMock = (name: string) => {
  let mock = sharedMocks.get(name);
  if (!mock) {
    mock = vi.fn();
    sharedMocks.set(name, mock);
  }
  return mock;
};

export const mockApiModule = async (importOriginal: () => Promise<Record<string, unknown>>) => {
  const actual = await importOriginal();
  const mocked: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(actual)) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const namespace: Record<string, unknown> = {};
      for (const methodName of Object.keys(value as Record<string, unknown>)) {
        namespace[methodName] = sharedMock(`${key}.${methodName}`);
      }
      mocked[key] = namespace;
    } else {
      mocked[key] = value;
    }
  }

  return mocked;
};
