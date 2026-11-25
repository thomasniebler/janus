import { z } from 'zod';
import api from './lib/httpClient';
import { parseResponse } from './lib/apiValidation';
import type { Parsed } from './lib/apiValidation';

/** The signed-in user's Telegram bot (JAN-405); the token is never returned. */
export const telegramStatusSchema = z.looseObject({
  connected: z.boolean(),
  bot_username: z.string().nullable().optional(),
  linked: z.boolean(),
  pairing_code: z.string().nullable().optional(),
  last_error: z.string().nullable().optional(),
});

export type TelegramStatus = z.infer<typeof telegramStatusSchema>;

export const telegramApi = {
  status: (): Parsed<TelegramStatus> => parseResponse(telegramStatusSchema)(api.get('/api/telegram')),
  connect: (token: string): Parsed<TelegramStatus> => parseResponse(telegramStatusSchema)(api.post('/api/telegram/connect', { token })),
  sendTest: () => api.post('/api/telegram/test'),
  disconnect: () => api.delete('/api/telegram'),
};
