import type { components } from './generated/api';
import * as rt from './lib/runtimeSchemas';
import api from './lib/httpClient';
import { parseResponse } from './lib/apiValidation';
import type { Parsed } from './lib/apiValidation';
import type { Webhook, WebhookSecret } from './types';

/** The user's inbound webhooks (JAN-406). A URL path and secret come back only from `create` / `rotateSecret`. */
export const webhooksApi = {
  list: (): Parsed<Webhook[]> => parseResponse(rt.webhookListSchema)(api.get('/api/webhooks')),
  create: (data: components['schemas']['WebhookCreate']): Parsed<WebhookSecret> =>
    parseResponse(rt.webhookSecretSchema)(api.post('/api/webhooks', data)),
  update: (id: number, data: Partial<components['schemas']['WebhookUpdate']>): Parsed<Webhook> =>
    parseResponse(rt.webhookSchema)(api.patch(`/api/webhooks/${id}`, data)),
  rotateSecret: (id: number): Parsed<WebhookSecret> =>
    parseResponse(rt.webhookSecretSchema)(api.post(`/api/webhooks/${id}/rotate-secret`)),
  remove: (id: number) => api.delete(`/api/webhooks/${id}`),
};
