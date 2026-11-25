import type { components } from './generated/api';
import * as rt from './lib/runtimeSchemas';
import api from './lib/httpClient';
import { parseResponse } from './lib/apiValidation';
import type { Parsed } from './lib/apiValidation';
import type { A2AAgent } from './types';

/** The user's remote A2A agents (JAN-404); re-exported from `api.ts`. */
export const a2aApi = {
  list: (): Parsed<A2AAgent[]> => parseResponse(rt.a2aAgentListSchema)(api.get('/api/tools/a2a-agents')),
  /** Register an agent (switched off); `token` is a write-only bearer token. */
  create: (data: components['schemas']['A2AAgentCreate']): Parsed<A2AAgent> =>
    parseResponse(rt.a2aAgentSchema)(api.post('/api/tools/a2a-agents', data)),
  update: (id: number, data: Partial<components['schemas']['A2AAgentUpdate']>): Parsed<A2AAgent> =>
    parseResponse(rt.a2aAgentSchema)(api.patch(`/api/tools/a2a-agents/${id}`, data)),
  refresh: (id: number): Parsed<A2AAgent> =>
    parseResponse(rt.a2aAgentSchema)(api.post(`/api/tools/a2a-agents/${id}/refresh`)),
  remove: (id: number) => api.delete(`/api/tools/a2a-agents/${id}`),
};
