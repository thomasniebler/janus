import type { KnowledgeGraph, WikiPageProvenance } from '../../lib/knowledgeStack';

/** Sample provenance of the "Acme renewal" topic page: one passage and one memory fact cited (JAN-334). */
export const sampleProvenance = (overrides: Partial<WikiPageProvenance> = {}): WikiPageProvenance => ({
  page_id: 1,
  subject: { type: 'topic', id: 5, title: 'Acme renewal', available: true },
  citations: [
    {
      n: 1,
      kind: 'passage',
      available: true,
      title: 'Acme renewal mail',
      date: '2026-09-20T08:00:00',
      source_type: 'email',
      chunk_id: 3,
      fact_id: null,
      source: { id: 2, type: 'email', title: 'Acme renewal mail', owner_id: 101, date: '2026-09-20T08:00:00' },
      excerpt: 'Hi Dana, Acme asks us to sign the renewal by 1 November. Northwind handles the hosting part.',
      fact: null,
      entity_ids: [7, 8],
    },
    {
      n: 2,
      kind: 'fact',
      available: true,
      title: 'Renewal budget is capped at 40k',
      date: '2026-09-18T10:00:00',
      source_type: 'memory_fact',
      chunk_id: null,
      fact_id: 31,
      source: null,
      excerpt: null,
      fact: {
        id: 31,
        summary: 'Renewal budget is capped at 40k',
        detail: 'Agreed with finance in September.',
        status: 'active',
        source: 'user',
        created_at: '2026-09-18T10:00:00',
        updated_at: '2026-09-18T10:00:00',
        citations: [2],
      },
      entity_ids: [],
    },
  ],
  entities: [
    { id: 7, name: 'Dana Whitfield', type: 'person', citations: [1], is_subject: false, wiki_page_id: 4 },
    { id: 8, name: 'Northwind', type: 'organization', citations: [1], is_subject: false, wiki_page_id: null },
  ],
  facts: [
    {
      id: 31,
      summary: 'Renewal budget is capped at 40k',
      detail: 'Agreed with finance in September.',
      status: 'active',
      source: 'user',
      created_at: '2026-09-18T10:00:00',
      updated_at: '2026-09-18T10:00:00',
      citations: [2],
    },
    {
      id: 32,
      summary: 'Dana prefers the Acme renewal call on Tuesdays',
      detail: null,
      status: 'active',
      source: 'agent',
      created_at: '2026-09-25T10:00:00',
      updated_at: '2026-09-25T10:00:00',
      citations: [],
    },
  ],
  ...overrides,
});

/** Sample graph: two topics, four entities, evidence and relation links. */
export const sampleGraph = (overrides: Partial<KnowledgeGraph> = {}): KnowledgeGraph => ({
  nodes: [
    { id: 'topic:5', kind: 'topic', ref_id: 5, label: 'Acme renewal', mentions: 0, wiki_page_id: 1 },
    { id: 'topic:6', kind: 'topic', ref_id: 6, label: 'Apollo migration', mentions: 0, wiki_page_id: null },
    { id: 'entity:7', kind: 'entity', ref_id: 7, label: 'Dana Whitfield', entity_type: 'person', mentions: 9, wiki_page_id: 4 },
    { id: 'entity:8', kind: 'entity', ref_id: 8, label: 'Northwind', entity_type: 'organization', mentions: 14, wiki_page_id: null },
    { id: 'entity:9', kind: 'entity', ref_id: 9, label: 'Acme', entity_type: 'organization', mentions: 6, wiki_page_id: null },
    { id: 'entity:10', kind: 'entity', ref_id: 10, label: 'Apollo', entity_type: 'project', mentions: 4, wiki_page_id: null },
  ],
  edges: [
    { source: 'topic:5', target: 'entity:7', kind: 'evidence', label: null, weight: 2 },
    { source: 'topic:5', target: 'entity:9', kind: 'evidence', label: null, weight: 3 },
    { source: 'topic:6', target: 'entity:10', kind: 'evidence', label: null, weight: 1 },
    { source: 'topic:6', target: 'entity:8', kind: 'evidence', label: null, weight: 1 },
    { source: 'entity:7', target: 'entity:8', kind: 'relation', label: 'works_for', weight: 1 },
  ],
  ...overrides,
});
