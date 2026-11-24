const trimTrailingSlash = (value: string): string => value.replace(/\/+$/, '');

const configuredApiBase = import.meta.env.VITE_API_URL?.trim();
const configuredWebSocketBase = import.meta.env.VITE_WS_URL?.trim();

const defaultApiBase = import.meta.env.DEV ? 'http://localhost:8080' : '';

export const apiBaseUrl = configuredApiBase ? trimTrailingSlash(configuredApiBase) : defaultApiBase;

const deriveWebSocketBaseUrl = (): string => {
  if (configuredWebSocketBase) {
    return trimTrailingSlash(configuredWebSocketBase);
  }

  if (configuredApiBase) {
    return trimTrailingSlash(configuredApiBase).replace(/^http/i, 'ws');
  }

  if (import.meta.env.DEV) {
    return 'ws://localhost:8080';
  }

  return '';
};

const websocketBaseUrl = deriveWebSocketBaseUrl();

export const buildWebSocketUrl = (path: string): string => {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;

  if (websocketBaseUrl) {
    return `${websocketBaseUrl}${normalizedPath}`;
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}${normalizedPath}`;
};
