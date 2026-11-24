import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  DOMAIN_QUERY_KEYS,
  parseDomainChangeFrame,
  queryKeysForDomains,
  startDomainChangeSync,
} from './domainChanges';

class FakeSocket {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;

  emit(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent);
  }

  drop() {
    this.onclose?.();
  }

  close() {
    this.closed = true;
  }
}

describe('queryKeysForDomains', () => {
  it('maps memory changes to the memory caches', () => {
    expect(queryKeysForDomains(['memory'])).toEqual([['memory-facts'], ['memory-compaction-latest']]);
  });

  it('de-duplicates shared prefixes and ignores unknown domains', () => {
    expect(queryKeysForDomains(['trigger_definitions', 'trigger_runs', 'not-a-domain'])).toEqual([
      ['trigger-definitions'],
      ['trigger-proposals'],
      ['runtime-triggers'],
    ]);
  });

  it('refreshes suggested automations and their nav badge on definition and tool-policy changes (JAN-235)', () => {
    // ['trigger-proposals'] is the prefix of the proposal list, history, and count (nav badge) queries.
    expect(queryKeysForDomains(['trigger_definitions'])).toContainEqual(['trigger-proposals']);
    expect(queryKeysForDomains(['tools'])).toContainEqual(['trigger-proposals']);
  });

  it('refreshes the dashboard widgets that read planner and topic data', () => {
    expect(queryKeysForDomains(['planner'])).toContainEqual(['dashboard', 'tasks']);
    expect(queryKeysForDomains(['topics'])).toContainEqual(['dashboard', 'topics']);
    expect(queryKeysForDomains(['whatsapp'])).toEqual([['dashboard', 'whatsapp-messages'], ['whatsapp-accounts']]);
    expect(queryKeysForDomains(['knowledge'])).toContainEqual(['knowledge-extraction']);
  });
});

describe('parseDomainChangeFrame', () => {
  it('reads ready and changed frames and drops everything else', () => {
    expect(parseDomainChangeFrame('{"type":"ready"}')).toEqual({ type: 'ready' });
    expect(parseDomainChangeFrame('{"type":"changed","domains":["skills",3]}')).toEqual({
      type: 'changed',
      domains: ['skills'],
    });
    expect(parseDomainChangeFrame('{"type":"ping"}')).toBeNull();
    expect(parseDomainChangeFrame('not json')).toBeNull();
    expect(parseDomainChangeFrame(new ArrayBuffer(2))).toBeNull();
  });
});

describe('startDomainChangeSync', () => {
  let sockets: FakeSocket[];
  let queryClient: QueryClient;
  let invalidate: ReturnType<typeof vi.spyOn>;

  const start = () =>
    startDomainChangeSync({
      queryClient,
      url: 'ws://janus/ws/changes',
      debounceMs: 50,
      initialReconnectMs: 100,
      createSocket: () => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket as unknown as WebSocket;
      },
    });

  const invalidatedKeys = () => invalidate.mock.calls.map(([filters]) => (filters as { queryKey: unknown }).queryKey);

  beforeEach(() => {
    vi.useFakeTimers();
    sockets = [];
    queryClient = new QueryClient();
    invalidate = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('coalesces a burst of changes into one debounced invalidation pass', () => {
    const stop = start();
    sockets[0].emit({ type: 'ready' });
    vi.advanceTimersByTime(50);
    invalidate.mockClear();
    sockets[0].emit({ type: 'changed', domains: ['memory'] });
    sockets[0].emit({ type: 'changed', domains: ['memory', 'skills'] });
    expect(invalidate).not.toHaveBeenCalled();

    vi.advanceTimersByTime(50);
    expect(invalidatedKeys()).toEqual([['memory-facts'], ['memory-compaction-latest'], ['skills']]);
    stop();
  });

  it('resyncs every domain on the first ready frame and ignores heartbeats', () => {
    const stop = start();
    sockets[0].emit({ type: 'ready' });
    vi.advanceTimersByTime(50);
    expect(invalidatedKeys()).toEqual(queryKeysForDomains(Object.keys(DOMAIN_QUERY_KEYS)));

    invalidate.mockClear();
    sockets[0].emit({ type: 'ping' });
    vi.advanceTimersByTime(1000);
    expect(invalidate).not.toHaveBeenCalled();
    stop();
  });

  it('reconnects after a drop and resyncs every domain once', () => {
    const stop = start();
    sockets[0].emit({ type: 'ready' });
    vi.advanceTimersByTime(50);
    invalidate.mockClear();
    sockets[0].drop();

    vi.advanceTimersByTime(100);
    expect(sockets).toHaveLength(2);
    sockets[1].emit({ type: 'ready' });
    vi.advanceTimersByTime(50);

    const expected = queryKeysForDomains(Object.keys(DOMAIN_QUERY_KEYS));
    expect(invalidatedKeys()).toEqual(expected);
    stop();
  });

  it('closes the socket and stops reconnecting when stopped', () => {
    const stop = start();
    stop();
    expect(sockets[0].closed).toBe(true);

    sockets[0].drop();
    vi.advanceTimersByTime(60000);
    expect(sockets).toHaveLength(1);
  });
});
