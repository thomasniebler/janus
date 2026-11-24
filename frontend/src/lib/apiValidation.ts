import type { AxiosResponse } from 'axios';
import type { z } from 'zod';

/**
 * Runtime validation of API responses at the client boundary (JAN-142).
 *
 * `api.ts` wraps a request with `parseResponse(schema)` so a response whose shape drifted
 * from what the UI renders fails at the API call, as an `ApiValidationError`, instead of
 * deep inside a component. The error and the console line carry only what is needed to
 * find the drift (method, path without its query string, and each issue's field path,
 * code and expectation); never the response body, a received value, or a query string,
 * which can hold mail content, credentials or search terms.
 */

/** One schema mismatch, reduced to where it is and what was expected. */
export interface ApiValidationIssue {
  /** Dotted field path, e.g. `0.email.sender`; `(root)` for the response itself. */
  path: string;
  code: string;
  /** What the schema expected (a type or the allowed values), when zod reports it. */
  expected?: string;
}

const MAX_REPORTED_ISSUES = 10;

/** Start of the console line for a failed validation; the e2e suite fails a test that logs it. */
const API_VALIDATION_LOG_PREFIX = '[api] Response validation failed';

export class ApiValidationError extends Error {
  readonly method: string;
  readonly endpoint: string;
  readonly issues: ApiValidationIssue[];

  constructor(method: string, endpoint: string, issues: ApiValidationIssue[]) {
    const where = issues.map((issue) => issue.path).join(', ');
    super(`Unexpected response from ${method} ${endpoint} (${where})`);
    this.name = 'ApiValidationError';
    this.method = method;
    this.endpoint = endpoint;
    this.issues = issues;
  }
}

export const isApiValidationError = (error: unknown): error is ApiValidationError =>
  error instanceof ApiValidationError;

/** Drops the query string (search terms, filters) and the origin, keeping the route path. */
const endpointOf = (url: string | undefined): string => {
  if (!url) {
    return '(unknown)';
  }
  const withoutQuery = url.split(/[?#]/, 1)[0];
  try {
    return new URL(withoutQuery, 'http://janus.invalid').pathname;
  } catch {
    return withoutQuery;
  }
};

const expectationOf = (issue: z.core.$ZodIssue): string | undefined => {
  if (issue.code === 'invalid_type') {
    return issue.expected;
  }
  if (issue.code === 'invalid_value') {
    return issue.values.map((value) => JSON.stringify(value)).join(' | ');
  }
  return undefined;
};

/** Reduces zod issues to path, code and expectation; zod messages and inputs are left out. */
export const summarizeIssues = (issues: readonly z.core.$ZodIssue[]): ApiValidationIssue[] =>
  issues.slice(0, MAX_REPORTED_ISSUES).map((issue) => {
    const expected = expectationOf(issue);
    return {
      path: issue.path.length > 0 ? issue.path.map(String).join('.') : '(root)',
      code: issue.code,
      ...(expected ? { expected } : {}),
    };
  });

/**
 * Validates `response.data` against `schema` and returns the response with the parsed data.
 * On a mismatch it logs one `console.error` line with the redacted context and throws an
 * `ApiValidationError`, which TanStack Query surfaces as the query's or mutation's error.
 */
export const validateResponse = <S extends z.ZodType>(
  schema: S,
  response: AxiosResponse<unknown>
): AxiosResponse<z.output<S>> => {
  const result = schema.safeParse(response.data);
  if (result.success) {
    return { ...response, data: result.data };
  }
  const method = (response.config?.method ?? 'get').toUpperCase();
  const endpoint = endpointOf(response.config?.url);
  const issues = summarizeIssues(result.error.issues);
  console.error(`${API_VALIDATION_LOG_PREFIX}: ${method} ${endpoint}`, { method, endpoint, issues });
  throw new ApiValidationError(method, endpoint, issues);
};

/** A validated wrapper's answer: annotating it proves the schema output fits the hand-authored type. */
export type Parsed<T> = Promise<AxiosResponse<T>>;

/** `parseResponse(schema)(api.get(...))`: awaits the request, then `validateResponse`. */
export const parseResponse =
  <S extends z.ZodType>(schema: S) =>
  async (request: Promise<AxiosResponse<unknown>>): Promise<AxiosResponse<z.output<S>>> =>
    validateResponse(schema, await request);
