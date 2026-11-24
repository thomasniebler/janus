import { useEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Decorator } from '@storybook/react';

/**
 * Shared Storybook decorator for components that fetch through TanStack Query.
 *
 * Storybook has no backend to talk to, so instead of mocking `axios`/MSW we seed
 * a fresh `QueryClient`'s cache directly via `setQueryData` before the story
 * renders. `staleTime: Infinity` + `retry: false` keep the seeded data as the
 * only data a story ever shows -- no background refetch races the story render
 * and fails against a nonexistent API.
 *
 * Usage:
 *   const meta = {
 *     ...,
 *     decorators: [withQueryData((queryClient) => {
 *       queryClient.setQueryData(['conversations'], []);
 *     })],
 *   } satisfies Meta<typeof MyComponent>;
 */
export function withQueryData(seed?: (queryClient: QueryClient) => void): Decorator {
  return (Story) => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: Infinity, gcTime: Infinity },
        mutations: { retry: false },
      },
    });
    seed?.(queryClient);
    return (
      <QueryClientProvider client={queryClient}>
        <Story />
      </QueryClientProvider>
    );
  };
}

/** A promise that never settles, for a "still loading" story that must not resolve. */
export function neverResolving<T = unknown>(): Promise<T> {
  return new Promise<T>(() => {});
}

/**
 * Temporarily replaces one method on an `api.ts` namespace object (e.g.
 * `emailApi.getAccounts`) for the lifetime of a story, restoring the original
 * on unmount. This is how "Loading" (pair with `neverResolving()`) and "Error"
 * (pair with `() => Promise.reject(new Error(...))`) stories are simulated for
 * components that fetch through TanStack Query -- there is no backend in
 * Storybook to actually respond, so the api module's own function is swapped
 * out rather than the network call intercepted.
 */
export function withApiOverride<T extends object, K extends keyof T>(target: T, method: K, impl: T[K]): Decorator {
  return (StoryFn) => {
    useEffect(() => {
      const original = target[method];
      target[method] = impl;
      return () => {
        target[method] = original;
      };
    }, []);
    return <StoryFn />;
  };
}

/**
 * A minimal fake `WebSocket` for components (like `BriefingChat`) that open one
 * on mount. Mirrors the shape `BriefingChat.test.tsx` stubs in vitest, so a
 * story can drive the same `__START__`/`__END__`/message protocol by calling
 * `.emit(...)` on the instance it creates, without ever reaching the network.
 */
export class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  url: string;
  // Reports itself open straight away so the chat session can send immediately.
  readyState: number = FakeWebSocket.OPEN;
  /** Every payload passed to `send()`, for tests that assert on outgoing frames. */
  sent: string[] = [];

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  close() {
    if (this.readyState === FakeWebSocket.CLOSED) return;
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  send(data: string) {
    this.sent.push(data);
  }

  emit(data: string) {
    this.onmessage?.({ data } as MessageEvent);
  }
}

/** Installs `FakeWebSocket` as `window.WebSocket` for the lifetime of a decorator. */
export function withFakeWebSocket(): Decorator {
  return (Story) => {
    FakeWebSocket.instances = [];
    (window as unknown as { WebSocket: unknown }).WebSocket = FakeWebSocket;
    return <Story />;
  };
}
