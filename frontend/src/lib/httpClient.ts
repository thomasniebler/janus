import axios from 'axios';
import type { InternalAxiosRequestConfig } from 'axios';
import { apiBaseUrl } from './network';

/**
 * The shared axios instance behind every API wrapper (`src/api.ts` and the `lib/*` API
 * modules): same-origin cookies plus the CSRF header on unsafe requests. `api.ts`
 * re-exports it as its default export.
 */
const api = axios.create({
  baseURL: apiBaseUrl,
  withCredentials: true,
});

let csrfTokenCache: string | null = null;
let csrfTokenRequest: Promise<string | null> | null = null;

const CSRF_HEADER = 'X-CSRF-Token';
const SAFE_METHODS = new Set(['get', 'head', 'options', 'trace']);

const shouldAttachCsrf = (config: InternalAxiosRequestConfig): boolean => {
  const method = config.method?.toLowerCase() ?? 'get';
  if (SAFE_METHODS.has(method)) {
    return false;
  }
  if (config.url?.startsWith('/api/auth/csrf')) {
    return false;
  }

  const authorizationHeader =
    typeof config.headers?.Authorization === 'string'
      ? config.headers.Authorization
      : typeof config.headers?.authorization === 'string'
        ? config.headers.authorization
        : null;
  return !authorizationHeader;
};

const fetchCsrfToken = async (): Promise<string | null> => {
  if (csrfTokenCache) {
    return csrfTokenCache;
  }
  if (csrfTokenRequest) {
    return csrfTokenRequest;
  }

  csrfTokenRequest = axios
    .get<{ csrf_token: string }>('/api/auth/csrf', {
      baseURL: apiBaseUrl,
      withCredentials: true,
    })
    .then((response) => {
      csrfTokenCache = response.data.csrf_token;
      return csrfTokenCache;
    })
    .catch(() => null)
    .finally(() => {
      csrfTokenRequest = null;
    });

  return csrfTokenRequest;
};

api.interceptors.request.use(async (config) => {
  if (!shouldAttachCsrf(config)) {
    return config;
  }

  const csrfToken = await fetchCsrfToken();
  if (csrfToken) {
    config.headers.set(CSRF_HEADER, csrfToken);
  }
  return config;
});

/** Remembers the CSRF token the login answer carried, or forgets it on logout. */
export const setCsrfToken = (token: string | null): void => {
  csrfTokenCache = token;
};

export default api;
