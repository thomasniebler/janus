import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { invokeMock } = vi.hoisted(() => ({
  invokeMock: vi.fn(),
}));

vi.mock('@tauri-apps/api/core', () => ({
  invoke: invokeMock,
}));

import { janusApi } from './janus';

describe('janusApi.sendChat', () => {
  beforeEach(() => {
    invokeMock.mockReset();
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: { __TAURI_INTERNALS__: {} },
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(globalThis, 'window');
  });

  it('invokes the registered Tauri chat command with the expected payload', async () => {
    const onEvent = vi.fn();
    invokeMock.mockResolvedValue('planned response');

    await janusApi.sendChat('Break this down', 'todo tree', onEvent);

    expect(invokeMock).toHaveBeenCalledWith('send_chat', {
      text: 'Break this down',
      pageContext: 'todo tree',
    });
    expect(onEvent).toHaveBeenCalledWith('planned response');
  });
});

describe('janusApi.login', () => {
  beforeEach(() => {
    invokeMock.mockReset();
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: { __TAURI_INTERNALS__: {} },
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(globalThis, 'window');
  });

  it('authenticates without requiring an agent id', async () => {
    invokeMock.mockResolvedValue({ connected: true });

    await janusApi.login('https://janus.example.com', 'alice', 'secret');

    expect(invokeMock).toHaveBeenCalledWith('login', {
      baseUrl: 'https://janus.example.com',
      username: 'alice',
      password: 'secret',
    });
  });
});
