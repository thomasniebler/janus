import api from './lib/httpClient';
import { parseResponse } from './lib/apiValidation';
import type { Parsed } from './lib/apiValidation';
import { z } from 'zod';

/** What a bulk dismissal changed; `groups` / `kept_ids` only for the duplicate cleanup. */
const bulkResultSchema = z.looseObject({
  dismissed_ids: z.array(z.number()),
  groups: z.number().optional(),
  kept_ids: z.array(z.number()).optional(),
});

const duplicateSummarySchema = z.looseObject({ groups: z.number(), duplicates: z.number() });

export type PlannerSuggestionBulkResult = z.infer<typeof bulkResultSchema>;

/** Bulk review of pending planner suggestions; dismissing never changes the planner itself. */
export const plannerSuggestionBulkApi = {
  /** Dismiss the given ids, or every pending suggestion with `{ all_pending: true }`. */
  dismiss: (data: { suggestion_ids?: number[]; all_pending?: boolean }): Parsed<PlannerSuggestionBulkResult> =>
    parseResponse(bulkResultSchema)(api.post('/api/planner/suggestions/dismiss', data)),
  duplicates: (): Parsed<z.infer<typeof duplicateSummarySchema>> =>
    parseResponse(duplicateSummarySchema)(api.get('/api/planner/suggestions/duplicates')),
  /** Keep the newest suggestion of each duplicate group and dismiss the rest. */
  dismissDuplicates: (): Parsed<PlannerSuggestionBulkResult> =>
    parseResponse(bulkResultSchema)(api.post('/api/planner/suggestions/dismiss-duplicates')),
};
