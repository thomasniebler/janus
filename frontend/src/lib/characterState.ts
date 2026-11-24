import type { AxiosResponse } from 'axios';
import api from '../api';
import { parseResponse } from './apiValidation';
import type { Parsed } from './apiValidation';
import {
  characterStateHistoryPageSchema,
  characterStateProposalListSchema,
  characterStateProposalSchema,
  characterStateViewSchema,
} from './runtimeSchemas';

/** Adaptive character state (JAN-301): closed value sets per dimension. */
export const CHARACTER_TONES = ['neutral', 'warm', 'formal', 'direct', 'playful'] as const;
export const CHARACTER_VERBOSITY = ['brief', 'balanced', 'detailed'] as const;
export const CHARACTER_PROACTIVITY = ['reactive', 'balanced', 'proactive'] as const;
export type CharacterTone = (typeof CHARACTER_TONES)[number];
export type CharacterVerbosity = (typeof CHARACTER_VERBOSITY)[number];
export type CharacterProactivity = (typeof CHARACTER_PROACTIVITY)[number];
export type CharacterStateDimension = 'tone' | 'verbosity' | 'proactivity';

export interface CharacterStateValues {
  tone: CharacterTone;
  verbosity: CharacterVerbosity;
  proactivity: CharacterProactivity;
  /** Free text; untrusted, always rendered as plain text. */
  current_focus: string | null;
}

export interface CharacterStateEnvelope {
  tone: { default: CharacterTone; allowed: CharacterTone[] };
  verbosity: { default: CharacterVerbosity; allowed: CharacterVerbosity[] };
  proactivity: { default: CharacterProactivity; allowed: CharacterProactivity[] };
  focus_allowed: boolean;
}

export interface CharacterStateView {
  state: CharacterStateValues;
  defaults: CharacterStateValues;
  envelope: CharacterStateEnvelope;
  revision: number;
  updated_at?: string | null;
  focus_max_chars: number;
}

export interface CharacterStateChange {
  id: number;
  revision: number;
  actor: 'owner' | 'janus';
  action: 'edit' | 'proposal' | 'rollback' | 'reset' | 'envelope';
  chat_turn_id?: string | null;
  proposal_id?: number | null;
  rollback_of_id?: number | null;
  previous_state: CharacterStateValues;
  new_state: CharacterStateValues;
  created_at: string;
}

export interface CharacterStateHistoryPage {
  items: CharacterStateChange[];
  /** Cursor for the next (older) page; `null` at the oldest change. */
  next_before_id: number | null;
}

export interface CharacterStateProposal {
  id: number;
  status: 'proposed' | 'approved' | 'rejected';
  changes: Partial<CharacterStateValues>;
  rationale: string;
  chat_turn_id?: string | null;
  conversation_id?: number | null;
  created_at: string;
  decided_at?: string | null;
}

/** Adaptive character state (JAN-301): Janus only proposes; every call here is the owner's. */
const stateView = (request: Promise<AxiosResponse<unknown>>): Parsed<CharacterStateView> =>
  parseResponse(characterStateViewSchema)(request);

export const characterStateApi = {
  get: () => stateView(api.get('/api/character/state')),
  update: (data: CharacterStateValues) => stateView(api.put('/api/character/state', data)),
  updateEnvelope: (data: CharacterStateEnvelope) => stateView(api.put('/api/character/state/envelope', data)),
  reset: () => stateView(api.post('/api/character/state/reset')),
  rollback: (changeId: number) => stateView(api.post('/api/character/state/rollback', { change_id: changeId })),
  /** One page of the audit trail, newest first; pass `next_before_id` back as `beforeId` for older rows. */
  history: (beforeId?: number | null): Parsed<CharacterStateHistoryPage> =>
    parseResponse(characterStateHistoryPageSchema)(
      api.get('/api/character/state/history', { params: beforeId ? { before_id: beforeId } : {} })
    ),
  proposals: (): Parsed<CharacterStateProposal[]> =>
    parseResponse(characterStateProposalListSchema)(api.get('/api/character/state/proposals')),
  approve: (proposalId: number) => stateView(api.post(`/api/character/state/proposals/${proposalId}/approve`)),
  reject: (proposalId: number): Parsed<CharacterStateProposal> =>
    parseResponse(characterStateProposalSchema)(api.post(`/api/character/state/proposals/${proposalId}/reject`)),
};
