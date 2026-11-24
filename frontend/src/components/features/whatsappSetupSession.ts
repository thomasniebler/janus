import axios from 'axios';
import type { WhatsAppSetupSession } from '../../types';

export const isPairingInProgress = (session: WhatsAppSetupSession | undefined) =>
  session?.setup_status === 'pairing' &&
  ['starting', 'waiting', 'code_ready'].includes(session.pairing_state);

export const getSetupSessionRefetchInterval = (session: WhatsAppSetupSession | undefined) =>
  isPairingInProgress(session) ? 2000 : false;

/** The API's `detail` (a string or validation errors) for a failed request, else `fallback`. */
export const getErrorMessage = (error: unknown, fallback: string): string => {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string' && detail.trim()) {
      return detail;
    }
    if (Array.isArray(detail)) {
      const message = detail
        .map((item) => (typeof item?.msg === 'string' ? item.msg : null))
        .filter((value): value is string => Boolean(value))
        .join(', ');
      if (message) {
        return message;
      }
    }
    if (error.message.trim()) {
      return error.message;
    }
  }

  return fallback;
};
