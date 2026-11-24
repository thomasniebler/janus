import type { AxiosResponse } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { z } from 'zod';
import { ApiValidationError, isApiValidationError, parseResponse, summarizeIssues, validateResponse } from './apiValidation';

const responseOf = (data: unknown, url = '/api/things', method = 'get'): AxiosResponse<unknown> =>
  ({ data, status: 200, statusText: 'OK', headers: {}, config: { url, method } }) as AxiosResponse<unknown>;

const thingSchema = z.object({
  id: z.number(),
  secret_note: z.string(),
  kind: z.enum(['a', 'b']),
});

describe('validateResponse', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('returns the response with the parsed data for a valid body', () => {
    const response = validateResponse(thingSchema, responseOf({ id: 1, secret_note: 'x', kind: 'a' }));
    expect(response.data).toEqual({ id: 1, secret_note: 'x', kind: 'a' });
    expect(response.status).toBe(200);
  });

  it('throws an ApiValidationError naming the method, path and fields, and logs no payload', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const body = { id: 'not-a-number', secret_note: 42, kind: 'top-secret-kind' };

    let thrown: unknown;
    try {
      validateResponse(thingSchema, responseOf(body, '/api/things/7?q=private+search', 'patch'));
    } catch (error) {
      thrown = error;
    }

    expect(isApiValidationError(thrown)).toBe(true);
    const error = thrown as ApiValidationError;
    expect(error.method).toBe('PATCH');
    expect(error.endpoint).toBe('/api/things/7');
    expect(error.issues).toEqual([
      { path: 'id', code: 'invalid_type', expected: 'number' },
      { path: 'secret_note', code: 'invalid_type', expected: 'string' },
      { path: 'kind', code: 'invalid_value', expected: '"a" | "b"' },
    ]);
    expect(error.message).toBe('Unexpected response from PATCH /api/things/7 (id, secret_note, kind)');

    expect(consoleError).toHaveBeenCalledTimes(1);
    const logged = JSON.stringify(consoleError.mock.calls[0]);
    expect(logged).toContain('/api/things/7');
    // Neither the received values nor the query string reach the log or the error.
    for (const leaked of ['not-a-number', '42', 'top-secret-kind', 'private', 'search']) {
      expect(logged).not.toContain(leaked);
      expect(JSON.stringify({ message: error.message, issues: error.issues })).not.toContain(leaked);
    }
  });

  it('reports a non-object body at the root', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(() => validateResponse(thingSchema, responseOf('<html>login</html>'))).toThrowError(
      /Unexpected response from GET \/api\/things \(\(root\)\)/
    );
  });

  it('strips the origin from an absolute URL', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    try {
      validateResponse(thingSchema, responseOf(null, 'https://janus.example/api/things?token=abc'));
    } catch (error) {
      expect((error as ApiValidationError).endpoint).toBe('/api/things');
    }
    expect.assertions(1);
  });
});

describe('parseResponse', () => {
  it('awaits the request before validating', async () => {
    const response = await parseResponse(z.array(z.number()))(Promise.resolve(responseOf([1, 2])));
    expect(response.data).toEqual([1, 2]);
  });

  it('rejects with the validation error', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    await expect(parseResponse(z.array(z.number()))(Promise.resolve(responseOf(['x'])))).rejects.toBeInstanceOf(
      ApiValidationError
    );
    vi.restoreAllMocks();
  });

  it('passes a failed request through untouched', async () => {
    const networkError = new Error('Network Error');
    await expect(parseResponse(z.number())(Promise.reject(networkError))).rejects.toBe(networkError);
  });
});

describe('summarizeIssues', () => {
  it('caps the number of reported issues', () => {
    const result = z.array(z.number()).safeParse(Array.from({ length: 25 }, () => 'x'));
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(summarizeIssues(result.error.issues)).toHaveLength(10);
    }
  });
});
