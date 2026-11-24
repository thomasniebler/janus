import axios from 'axios';

/**
 * The message to show for a failed request: the backend's `detail` when it sent one
 * (such as why a non-admin may not save), else the request's own error, else `fallback`.
 */
export const requestErrorMessage = (error: unknown, fallback: string): string => {
  if (!axios.isAxiosError(error)) return fallback;
  const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail;
  return typeof detail === 'string' ? detail : error.message;
};
