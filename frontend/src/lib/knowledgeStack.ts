import api from '../api';
import { parseResponse } from './apiValidation';
import type { Parsed } from './apiValidation';
import { knowledgeGraphSchema, wikiPageProvenanceSchema } from './knowledgeSchemas';

/**
 * Types and calls for the knowledge stack views (JAN-334): a wiki page's provenance and the topic/entity
 * graph. Kept out of `api.ts`/`types.ts`, which are held at their size (eslint.baseline.json). Tests and
 * stories stub these through `vi.spyOn(knowledgeStackApi, …)` / `withApiOverride`.
 */

export interface ProvenanceFact {
  id: number;
  summary: string;
  detail?: string | null;
  status?: string | null;
  source?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  /** The `[n]` markers citing this fact; empty for a fact the next synthesis would use. */
  citations: number[];
}

export interface ProvenanceCitation {
  n: number;
  kind: 'passage' | 'fact';
  /** False when the cited passage or fact is gone since the page was written. */
  available: boolean;
  title?: string | null;
  date?: string | null;
  source_type?: string | null;
  chunk_id?: number | null;
  fact_id?: number | null;
  source?: { id: number; type: string; title?: string | null; owner_id?: number | null; date?: string | null } | null;
  /** The raw passage text (passages only). */
  excerpt?: string | null;
  /** The raw memory fact (facts only). */
  fact?: ProvenanceFact | null;
  entity_ids: number[];
}

interface ProvenanceEntity {
  id: number;
  name: string;
  type: string;
  citations: number[];
  is_subject: boolean;
  wiki_page_id?: number | null;
}

export interface WikiPageProvenance {
  page_id: number;
  subject: { type: 'topic' | 'entity'; id?: number | null; title: string; available: boolean };
  citations: ProvenanceCitation[];
  entities: ProvenanceEntity[];
  facts: ProvenanceFact[];
}

export interface KnowledgeGraphNode {
  id: string;
  kind: 'topic' | 'entity';
  ref_id: number;
  label: string;
  entity_type?: string | null;
  mentions: number;
  wiki_page_id?: number | null;
}

interface KnowledgeGraphEdge {
  source: string;
  target: string;
  /** `evidence`: the topic's sources mention the entity; `relation`: an extracted entity relation. */
  kind: 'evidence' | 'relation';
  label?: string | null;
  weight: number;
}

export interface KnowledgeGraph {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
}

export const knowledgeStackApi = {
  getProvenance: (pageId: number): Parsed<WikiPageProvenance> =>
    parseResponse(wikiPageProvenanceSchema)(api.get(`/api/wiki/pages/${pageId}/provenance`)),
  getGraph: (params?: { topics?: number; entities?: number }): Parsed<KnowledgeGraph> =>
    parseResponse(knowledgeGraphSchema)(api.get('/api/knowledge/graph', { params })),
};
