import type { CharacterStateChange, CharacterStateProposal, CharacterStateView } from '../../../lib/characterState';

/** Shared story and test data for the adaptive character state (JAN-301). */
export const characterStateView: CharacterStateView = {
  state: { tone: 'warm', verbosity: 'brief', proactivity: 'balanced', current_focus: 'Quarterly report' },
  defaults: { tone: 'neutral', verbosity: 'balanced', proactivity: 'balanced', current_focus: null },
  envelope: {
    tone: { default: 'neutral', allowed: ['neutral', 'warm', 'formal', 'direct', 'playful'] },
    verbosity: { default: 'balanced', allowed: ['brief', 'balanced', 'detailed'] },
    proactivity: { default: 'balanced', allowed: ['reactive', 'balanced', 'proactive'] },
    focus_allowed: true,
  },
  revision: 2,
  updated_at: '2026-10-12T09:30:00Z',
  focus_max_chars: 200,
};

export const characterStateHistory: CharacterStateChange[] = [
  {
    id: 2,
    revision: 2,
    actor: 'janus',
    action: 'proposal',
    chat_turn_id: 'chat-conversation-4::message-2',
    proposal_id: 1,
    previous_state: { tone: 'warm', verbosity: 'balanced', proactivity: 'balanced', current_focus: 'Quarterly report' },
    new_state: characterStateView.state,
    created_at: '2026-10-12T09:30:00Z',
  },
  {
    id: 1,
    revision: 1,
    actor: 'owner',
    action: 'edit',
    previous_state: characterStateView.defaults,
    new_state: { tone: 'warm', verbosity: 'balanced', proactivity: 'balanced', current_focus: 'Quarterly report' },
    created_at: '2026-10-11T17:00:00Z',
  },
];

export const characterStateProposals: CharacterStateProposal[] = [
  {
    id: 3,
    status: 'proposed',
    changes: { proactivity: 'proactive' },
    rationale: 'You asked me to suggest next steps without being asked.',
    chat_turn_id: 'chat-conversation-4::message-5',
    conversation_id: 4,
    created_at: '2026-10-12T10:00:00Z',
  },
  {
    id: 4,
    status: 'proposed',
    changes: { current_focus: '<img src=x onerror=alert(1)> Tax return' },
    rationale: 'You mentioned your tax return twice today.',
    created_at: '2026-10-12T10:05:00Z',
  },
];
