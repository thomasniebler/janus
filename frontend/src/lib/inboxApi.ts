import { z } from 'zod';
import api from './httpClient';
import { parseResponse, type Parsed } from './apiValidation';

/** A device token without its secret. */
const deviceTokenSchema = z.looseObject({
  id: z.number(),
  name: z.string(),
  token_prefix: z.string(),
  created_at: z.string(),
  last_used_at: z.string().nullable().default(null),
});

/** A freshly created token: `token` is shown once and cannot be read again. */
const deviceTokenCreatedSchema = deviceTokenSchema.extend({ token: z.string() });

export type DeviceToken = z.infer<typeof deviceTokenSchema>;
export type DeviceTokenCreated = z.infer<typeof deviceTokenCreatedSchema>;

/** Device tokens for scripts and devices that may use only the action inbox (web session only). */
export const inboxApi = {
  listTokens: (): Parsed<DeviceToken[]> => parseResponse(z.array(deviceTokenSchema))(api.get('/api/inbox/tokens')),
  createToken: (name: string): Parsed<DeviceTokenCreated> =>
    parseResponse(deviceTokenCreatedSchema)(api.post('/api/inbox/tokens', { name })),
  revokeToken: (id: number) => api.delete(`/api/inbox/tokens/${id}`),
};
