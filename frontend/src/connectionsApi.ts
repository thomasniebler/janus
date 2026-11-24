import * as opsSchemas from './lib/opsSchemas';
import * as schemas from './lib/schemas';
import api from './lib/httpClient';
import { parseResponse } from './lib/apiValidation';
import type { Parsed } from './lib/apiValidation';
import type { ConnectionProvider, ConnectionsOverview, EmailAccount, GoogleCalendarOption } from './types';

/** The Google and Microsoft accounts the user connected (Settings -> Connections). */
export const connectionsApi = {
  getOverview: (): Parsed<ConnectionsOverview> =>
    parseResponse(opsSchemas.connectionsOverviewSchema)(api.get('/api/connections')),
  /** The provider's sign-in URL; the browser is sent to it and comes back to `/api/connections/<provider>/callback`. */
  getAuthUrl: (provider: ConnectionProvider): Parsed<{ url: string }> =>
    parseResponse(opsSchemas.authUrlSchema)(api.get(`/api/connections/${provider}/auth-url`)),
  /** The connected account's calendars with the current selection (Google and Outlook alike). */
  getCalendars: (provider: ConnectionProvider): Parsed<GoogleCalendarOption[]> =>
    parseResponse(opsSchemas.calendarListSchema)(api.get(`/api/planner/${provider}/calendars`)),
  updateCalendars: (provider: ConnectionProvider, calendarIds: string[]): Parsed<GoogleCalendarOption[]> =>
    parseResponse(opsSchemas.calendarListSchema)(api.put(`/api/planner/${provider}/calendars`, { calendar_ids: calendarIds })),
  disconnect: (provider: ConnectionProvider) => api.delete(`/api/connections/${provider}`),
  /** Adds a Gmail / Outlook mailbox on the connected account (IMAP over OAuth). */
  addMailbox: (provider: ConnectionProvider, name?: string): Parsed<EmailAccount> =>
    parseResponse(schemas.emailAccountSchema)(api.post(`/api/connections/${provider}/mailbox`, name ? { name } : {})),
};
