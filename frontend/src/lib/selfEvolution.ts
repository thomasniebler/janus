import api from '../api';

/** One recorded action of the self-evolution loop (JAN-320). */
interface SelfEvolutionAction {
  id: number;
  action: string;
  status: string;
  subject: string | null;
  detail: string | null;
  data: Record<string, unknown>;
  created_at: string | null;
}

/** The caller's self-evolution settings, caps, usage and audit trail; never the token. */
export interface SelfEvolutionSettings {
  enabled: boolean;
  /** The Claude Code Routine's API trigger id (trig_...); the fire URL is built by the backend. */
  routine_trigger_id: string | null;
  routine_token_configured: boolean;
  github_repo: string | null;
  production_base_url: string | null;
  daily_caps: Record<string, number>;
  usage_last_24h: Record<string, number>;
  scope_fence: string[];
  recent_actions: SelfEvolutionAction[];
}

/** Partial update: omitted leaves a field alone, '' (or {}) clears it. Admin-only. */
export interface SelfEvolutionSettingsUpdate {
  enabled?: boolean | null;
  routine_trigger_id?: string | null;
  routine_token?: string | null;
  github_repo?: string | null;
  production_base_url?: string | null;
}

export const selfEvolutionApi = {
  getSettings: () => api.get<SelfEvolutionSettings>('/api/self-evolution/settings'),
  updateSettings: (data: SelfEvolutionSettingsUpdate) =>
    api.patch<SelfEvolutionSettings>('/api/self-evolution/settings', data),
};
