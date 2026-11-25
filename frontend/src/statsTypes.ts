import type { TriggerDefinitionOrigin } from './types';

/** Owner-scoped activity statistics for Settings → Stats (JAN-258). Dates are local `YYYY-MM-DD`. */
export type StatsRangeDays = 7 | 30 | 90;

export interface TriggerDefinitionStats {
  /** `null` for ad-hoc runs without (or with a deleted) definition. */
  definition_id: number | null;
  name: string | null;
  origin: TriggerDefinitionOrigin | null;
  runs: number;
  completed: number;
  failed: number;
  cancelled: number;
  pending: number;
  /** completed / (completed + failed); null when nothing has finished. */
  success_rate: number | null;
  avg_duration_seconds: number | null;
  last_run_at: string;
}

/** Review outcomes of one kind of Janus proposal (JAN-371). */
export interface DecisionStats<K extends string> {
  totals: Record<K, number>;
  total: number;
  /** approved / (approved + rejected or denied); null when nothing was decided. */
  approval_rate: number | null;
  /** Median hours from proposal to decision; null when none is recorded. */
  median_hours_to_decision: number | null;
}

export interface StatsSummary {
  days: StatsRangeDays;
  timezone: string;
  start: string;
  end: string;
  dates: string[];
  mail: {
    per_day: { date: string; important: number; digest: number; ignore: number; spam: number }[];
    totals: Record<'important' | 'digest' | 'ignore' | 'spam', number>;
    total: number;
  };
  triggers: {
    per_day: { date: string; completed: number; failed: number; cancelled: number; pending: number }[];
    totals: Record<'completed' | 'failed' | 'cancelled' | 'pending', number>;
    total: number;
    success_rate: number | null;
    definitions: TriggerDefinitionStats[];
  };
  memory: {
    per_day: { date: string; new_facts: number }[];
    new_facts: number;
    active_facts: number;
    archived_facts: number;
    active_topics: number;
    total_topics: number;
    wiki_pages: number;
    knowledge_sources: number;
  };
  messaging: {
    per_day: { date: string; whatsapp_in: number; whatsapp_out: number; chat_user: number; chat_assistant: number }[];
    totals: Record<'whatsapp_in' | 'whatsapp_out' | 'chat_user' | 'chat_assistant', number>;
  };
  approvals: {
    automations: DecisionStats<'proposed' | 'approved' | 'rejected' | 'withdrawn' | 'superseded'>;
    chat_tools: DecisionStats<'approved' | 'denied' | 'timeout' | 'disconnected' | 'unavailable'>;
    character: DecisionStats<'proposed' | 'approved' | 'rejected'>;
  };
  /** LLM calls and tokens (JAN-408); days follow UTC. */
  llm: {
    per_day: { date: string; calls: number; input_tokens: number; output_tokens: number }[];
    by_surface: { surface: string; calls: number; input_tokens: number; output_tokens: number }[];
    by_model: { model: string; calls: number; input_tokens: number; output_tokens: number }[];
    totals: Record<'calls' | 'input_tokens' | 'output_tokens' | 'unreported_calls', number>;
  };
}
