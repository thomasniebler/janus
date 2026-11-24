import React from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { AxiosError, AxiosHeaders } from 'axios';
import { QueryClient, QueryClientProvider, focusManager, useQuery } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type ApiModule = typeof import('../api');
type SetupStatusModule = typeof import('./setupStatus');
let authApi: ApiModule['authApi'];
let setupStatusQueryOptions: SetupStatusModule['setupStatusQueryOptions'];

const notFound = () =>
  new AxiosError('Not Found', 'ERR_BAD_REQUEST', undefined, undefined, {
    status: 404,
    statusText: 'Not Found',
    data: { detail: 'Not Found' },
    headers: {},
    config: { headers: new AxiosHeaders() },
  });

const renderSetupStatus = () => {
  const client = new QueryClient();
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useQuery(setupStatusQueryOptions), { wrapper });
};

describe('setupStatusQueryOptions', () => {
  // The module registry is shared across test files (`isolate: false`), so a cached
  // `./setupStatus` can be bound to another file's mocked `../api` that this file never
  // sees, and spying on `authApi` here then missed it depending on file order. Loading both
  // fresh makes `./setupStatus` use the same `authApi` this file stubs.
  beforeEach(async () => {
    vi.resetModules();
    ({ authApi } = await import('../api'));
    ({ setupStatusQueryOptions } = await import('./setupStatus'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
    focusManager.setFocused(undefined);
  });

  it('treats a 404 (public setup disabled) as "no setup needed" and never reloads on refocus', async () => {
    const getSetupStatus = vi.spyOn(authApi, 'getSetupStatus').mockRejectedValue(notFound());
    const { result } = renderSetupStatus();

    await waitFor(() => expect(result.current.data).toEqual({ needs_setup: false }));
    expect(result.current.isError).toBe(false);

    // A window refocus used to put this data-less query back into `isLoading`,
    // which made App swap the whole shell for the loader (JAN-182).
    act(() => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });

    expect(result.current.isLoading).toBe(false);
    expect(getSetupStatus).toHaveBeenCalledTimes(1);
  });

  it('keeps a pending setup refreshable so a finished setup elsewhere is noticed on refocus', async () => {
    const getSetupStatus = vi
      .spyOn(authApi, 'getSetupStatus')
      .mockResolvedValueOnce({ data: { needs_setup: true } } as Awaited<ReturnType<typeof authApi.getSetupStatus>>)
      .mockResolvedValueOnce({ data: { needs_setup: false } } as Awaited<ReturnType<typeof authApi.getSetupStatus>>);
    const { result } = renderSetupStatus();

    await waitFor(() => expect(result.current.data).toEqual({ needs_setup: true }));

    act(() => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });

    expect(result.current.isLoading).toBe(false);
    await waitFor(() => expect(result.current.data).toEqual({ needs_setup: false }));
    expect(getSetupStatus).toHaveBeenCalledTimes(2);
  });

  it('still surfaces other errors', async () => {
    vi.spyOn(authApi, 'getSetupStatus').mockRejectedValue(new Error('boom'));
    const { result } = renderSetupStatus();

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
