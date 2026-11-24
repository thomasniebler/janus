import api from '../api';

/** The optional model-based prompt-injection check's settings and this month's usage (JAN-289). */
export interface InjectionClassifierStatus {
  enabled: boolean;
  /** `provider/model` of the utility model that does the check. */
  model: string;
  is_cloud: boolean;
  monthly_token_budget: number;
  period: string;
  tokens_used: number;
  calls: number;
  cached_verdicts: number;
}

export const injectionClassifierApi = {
  getStatus: () => api.get<InjectionClassifierStatus>('/api/tools/injection-classifier'),
  update: (payload: { enabled?: boolean; monthly_token_budget?: number }) =>
    api.put<InjectionClassifierStatus>('/api/tools/injection-classifier', payload),
};
