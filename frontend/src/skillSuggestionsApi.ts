import api from './lib/httpClient';
import { parseResponse } from './lib/apiValidation';
import type { Parsed } from './lib/apiValidation';
import { z } from 'zod';

/** A repeated tool sequence from the user's chats that could become a skill (JAN-407). */
export const skillSuggestionSchema = z.looseObject({
  key: z.string(),
  tools: z.array(z.string()),
  turns: z.number().int(),
  examples: z.array(z.string()),
  brief: z.string(),
});

export type SkillSuggestion = z.infer<typeof skillSuggestionSchema>;

export const skillSuggestionsApi = {
  list: (): Parsed<SkillSuggestion[]> => parseResponse(z.array(skillSuggestionSchema))(api.get('/api/skills/suggestions')),
  /** Hide a suggestion for 30 days. */
  dismiss: (key: string) => api.post('/api/skills/suggestions/dismiss', { key }),
};
