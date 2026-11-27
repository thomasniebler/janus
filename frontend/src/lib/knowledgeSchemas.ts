import { z } from 'zod';
import { topicSummarySchema } from './schemas';

/**
 * Response schemas for what Janus knows (JAN-335, following JAN-142): topics, memory facts,
 * conversations, knowledge sources/entities/search, wiki pages and the knowledge stack
 * (provenance and graph). Every object is a `z.looseObject`, so a field the backend adds
 * passes through; the wrappers annotate their answer with the hand-authored type
 * (`Parsed<T>`), so `tsc` proves each schema's output fits it.
 */


// --- Topics -----------------------------------------------------------------

const topicEvidenceSchema = z.looseObject({
  id: z.number().int(),
  source_type: z.enum(['email', 'reminder', 'calendar_event', 'task', 'memory_fact']),
  source_id: z.number().int().nullable().optional(),
  excerpt: z.string().nullable().optional(),
  evidence_role: z.enum(['trigger', 'supporting', 'derived_link']),
  created_at: z.string(),
});

export const topicDetailSchema = topicSummarySchema.extend({
  evidence: z.array(topicEvidenceSchema),
});

// --- Memory -----------------------------------------------------------------

export const memoryFactSchema = z.looseObject({
  id: z.number().int(),
  summary: z.string(),
  detail: z.string().nullable(),
  source: z.enum(['user', 'agent', 'compaction']),
  status: z.enum(['active', 'archived']),
  source_conversation_id: z.number().int().nullable(),
  compacted_into_id: z.number().int().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  last_accessed_at: z.string().nullable(),
});

export const memoryFactListSchema = z.array(memoryFactSchema);

export const memoryCompactionRunSchema = z.looseObject({
  id: z.number().int(),
  status: z.enum(['queued', 'running', 'completed', 'failed']),
  result_summary: z.string().nullable(),
  error_message: z.string().nullable(),
  facts_considered: z.number().int(),
  facts_archived: z.number().int(),
  facts_created: z.number().int(),
  created_at: z.string(),
  started_at: z.string().nullable(),
  completed_at: z.string().nullable(),
});

/** `GET /api/memory/compact/latest`: `null` before the first run. */
export const latestMemoryCompactionRunSchema = memoryCompactionRunSchema.nullable();

// --- Conversations ----------------------------------------------------------

export const conversationSummarySchema = z.looseObject({
  id: z.number().int(),
  title: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  // Janus-started conversations (JAN-319); absent on older payloads.
  origin: z.enum(['user', 'janus']).optional(),
  unread: z.boolean().optional(),
});

export const conversationSummaryListSchema = z.array(conversationSummarySchema);

/** A stored tool call (JAN-249); `chatFrames.toToolCallRecords` re-reads it before it renders. */
const storedToolCallSchema = z.looseObject({
  call_id: z.string(),
  tool_id: z.string().nullable(),
  tool_name: z.string(),
  phase: z.enum(['start', 'end', 'error', 'blocked']),
  args: z.unknown(),
  args_truncated: z.boolean(),
  result_preview: z.string().nullable(),
  result_truncated: z.boolean(),
  duration_ms: z.number().nullable(),
  approval: z.enum(['approved', 'denied', 'timeout', 'disconnected', 'unavailable']).nullable().optional(),
});

export const conversationDetailSchema = conversationSummarySchema.extend({
  messages: z.array(
    z.looseObject({
      id: z.number().int(),
      role: z.enum(['user', 'assistant']),
      content: z.string(),
      created_at: z.string(),
      tool_calls: z.array(storedToolCallSchema).optional(),
    })
  ),
});

// --- Knowledge --------------------------------------------------------------

const knowledgeSourceTypeSchema = z.enum([
  'email',
  'email_attachment',
  'conversation',
  'whatsapp_conversation',
  'calendar_event',
  'planner_task',
  'upload',
]);
const knowledgeEntityTypeSchema = z.enum(['person', 'organization', 'project', 'place', 'product', 'concept']);

export const knowledgeSourceSchema = z.looseObject({
  id: z.number().int(),
  source_type: knowledgeSourceTypeSchema,
  owner_id: z.number().int().nullable().optional(),
  part: z.number().int(),
  title: z.string().nullable().optional(),
  mime_type: z.string().nullable().optional(),
  size_bytes: z.number().int().nullable().optional(),
  status: z.enum(['pending', 'ready', 'skipped', 'failed']),
  status_detail: z.string().nullable().optional(),
  chunk_count: z.number().int(),
  processed_at: z.string().nullable().optional(),
  created_at: z.string(),
});

export const knowledgeSourceListSchema = z.array(knowledgeSourceSchema);

export const knowledgeExtractionStatusSchema = z.looseObject({
  enabled: z.boolean(),
  definition_id: z.number().int().nullable().optional(),
  model: z.string(),
  is_cloud: z.boolean(),
  monthly_token_budget: z.number().int(),
  period: z.string(),
  tokens_used: z.number().int(),
  calls: z.number().int(),
  pending_chunks: z.number().int(),
});

export const knowledgeEntitySchema = z.looseObject({
  id: z.number().int(),
  entity_type: knowledgeEntityTypeSchema,
  name: z.string(),
  aliases: z.array(z.string()),
  description: z.string().nullable().optional(),
  mention_count: z.number().int(),
  relation_count: z.number().int(),
});

export const knowledgeEntityListSchema = z.array(knowledgeEntitySchema);

export const knowledgeMergeSuggestionListSchema = z.array(
  z.looseObject({
    id: z.number().int(),
    reason: z.string(),
    created_at: z.string(),
    entity: knowledgeEntitySchema,
    duplicate: knowledgeEntitySchema,
  })
);

const knowledgeEntityRefSchema = z.looseObject({
  id: z.number().int(),
  name: z.string(),
  type: knowledgeEntityTypeSchema,
});

const knowledgeSourceRefSchema = z.looseObject({
  id: z.number().int(),
  type: knowledgeSourceTypeSchema,
  title: z.string().nullable().optional(),
  owner_id: z.number().int().nullable().optional(),
  date: z.string().nullable().optional(),
});

export const knowledgeSearchResultSchema = z.looseObject({
  hits: z.array(
    z.looseObject({
      chunk_id: z.number().int(),
      text: z.string(),
      source: knowledgeSourceRefSchema,
      entities: z.array(knowledgeEntityRefSchema),
      matched_by: z.array(z.string()),
    })
  ),
  entities: z.array(knowledgeEntityRefSchema),
});

export const knowledgeEntityDetailSchema = knowledgeEntityRefSchema.extend({
  aliases: z.array(z.string()),
  description: z.string().nullable().optional(),
  relations: z.array(
    z.looseObject({
      id: z.number().int(),
      direction: z.enum(['outgoing', 'incoming']),
      predicate: z.string(),
      other: knowledgeEntityRefSchema,
      confidence: z.string(),
      supporting_chunks: z.number().int(),
    })
  ),
  sources: z.array(knowledgeSourceRefSchema.extend({ mentions: z.number().int() })),
});

// --- Wiki -------------------------------------------------------------------

const wikiCitationSchema = z.looseObject({
  n: z.number().int(),
  chunk_id: z.number().int().nullable().optional(),
  // Citations of a memory fact (wiki_synthesis.FACT_SOURCE_TYPE) carry `fact_id` and `memory_fact`.
  fact_id: z.number().int().nullable().optional(),
  source_id: z.number().int().nullable().optional(),
  source_type: z.union([knowledgeSourceTypeSchema, z.literal('memory_fact')]).nullable().optional(),
  title: z.string().nullable().optional(),
  date: z.string().nullable().optional(),
});

export const wikiPageSummarySchema = z.looseObject({
  id: z.number().int(),
  slug: z.string(),
  title: z.string(),
  subject_type: z.enum(['topic', 'entity']),
  subject_id: z.number().int().nullable().optional(),
  user_edited: z.boolean(),
  synthesized_at: z.string().nullable().optional(),
  updated_at: z.string(),
  pending_proposals: z.number().int(),
});

export const wikiPageSummaryListSchema = z.array(wikiPageSummarySchema);

const wikiRevisionSchema = z.looseObject({
  id: z.number().int(),
  body: z.string(),
  citations: z.array(wikiCitationSchema),
  author: z.enum(['janus', 'user', 'agent']),
  kind: z.enum(['published', 'proposed']),
  status: z.enum(['pending', 'accepted', 'dismissed']).nullable().optional(),
  note: z.string().nullable().optional(),
  created_at: z.string(),
  resolved_at: z.string().nullable().optional(),
});

export const wikiPageDetailSchema = wikiPageSummarySchema.extend({
  body: z.string(),
  citations: z.array(wikiCitationSchema),
  proposals: z.array(wikiRevisionSchema),
  revisions: z.array(wikiRevisionSchema),
});

export const wikiSynthesisStatusSchema = z.looseObject({
  enabled: z.boolean(),
  definition_id: z.number().int().nullable().optional(),
  model: z.string(),
  is_cloud: z.boolean(),
  monthly_token_budget: z.number().int(),
  period: z.string(),
  tokens_used: z.number().int(),
});

// --- Knowledge stack: provenance and graph (JAN-334) ------------------------

const provenanceFactSchema = z.looseObject({
  id: z.number().int(),
  summary: z.string(),
  detail: z.string().nullable().optional(),
  status: z.string().nullable().optional(),
  source: z.string().nullable().optional(),
  created_at: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
  citations: z.array(z.number().int()),
});

export const wikiPageProvenanceSchema = z.looseObject({
  page_id: z.number().int(),
  subject: z.looseObject({
    type: z.enum(['topic', 'entity']),
    id: z.number().int().nullable().optional(),
    title: z.string(),
    available: z.boolean(),
  }),
  citations: z.array(
    z.looseObject({
      n: z.number().int(),
      kind: z.enum(['passage', 'fact']),
      available: z.boolean(),
      title: z.string().nullable().optional(),
      date: z.string().nullable().optional(),
      source_type: z.string().nullable().optional(),
      chunk_id: z.number().int().nullable().optional(),
      fact_id: z.number().int().nullable().optional(),
      source: z
        .looseObject({
          id: z.number().int(),
          type: z.string(),
          title: z.string().nullable().optional(),
          owner_id: z.number().int().nullable().optional(),
          date: z.string().nullable().optional(),
        })
        .nullable()
        .optional(),
      excerpt: z.string().nullable().optional(),
      fact: provenanceFactSchema.nullable().optional(),
      entity_ids: z.array(z.number().int()),
    })
  ),
  entities: z.array(
    z.looseObject({
      id: z.number().int(),
      name: z.string(),
      type: z.string(),
      citations: z.array(z.number().int()),
      is_subject: z.boolean(),
      wiki_page_id: z.number().int().nullable().optional(),
    })
  ),
  facts: z.array(provenanceFactSchema),
});

export const knowledgeGraphSchema = z.looseObject({
  nodes: z.array(
    z.looseObject({
      id: z.string(),
      kind: z.enum(['topic', 'entity']),
      ref_id: z.number().int(),
      label: z.string(),
      entity_type: z.string().nullable().optional(),
      mentions: z.number().int(),
      wiki_page_id: z.number().int().nullable().optional(),
    })
  ),
  edges: z.array(
    z.looseObject({
      source: z.string(),
      target: z.string(),
      kind: z.enum(['evidence', 'relation']),
      label: z.string().nullable().optional(),
      weight: z.number().int(),
    })
  ),
});
