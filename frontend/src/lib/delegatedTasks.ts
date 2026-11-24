import api from '../api';
import { parseResponse } from './apiValidation';
import type { Parsed } from './apiValidation';
import { delegatedTaskPageSchema } from './runtimeSchemas';
import type { components } from '../generated/api';

type Schemas = components['schemas'];

/** Lifecycle of one delegated child task (JAN-200), generated from the backend enum. */
export type DelegatedTaskStatus = Schemas['RuntimeDelegationStatus'];
/** One task Janus delegated from a conversation (JAN-208). `objective`, `result` and `error` are untrusted text. */
export type DelegatedTask = Schemas['DelegationResponse'];
/** One newest-first page of a conversation's delegated tasks plus pagination metadata. */
export type DelegatedTaskPage = Schemas['DelegationPage'];

export const delegatedTasksApi = {
  /** `GET /api/conversations/{id}/delegations`: newest first; 404 for a missing or foreign conversation. */
  list: (conversationId: number, params?: { limit?: number; offset?: number }): Parsed<DelegatedTaskPage> =>
    parseResponse(delegatedTaskPageSchema)(api.get(`/api/conversations/${conversationId}/delegations`, { params })),
};
