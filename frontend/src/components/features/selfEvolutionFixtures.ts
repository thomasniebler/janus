import type { SelfEvolutionSettings } from '../../lib/selfEvolution';

/** Shared self-evolution settings for stories and tests (JAN-320). */
const CAPS = {
  issue_filed: 5,
  job_started: 3,
  feature_merged: 3,
  release_merged: 3,
  self_check: 3,
  revert_opened: 3,
};
const NO_USAGE = Object.fromEntries(Object.keys(CAPS).map((key) => [key, 0]));
const FENCE = ['src/janus/security.py', 'src/janus/tool_policy.py', '.github/', 'alembic/'];

export const unconfiguredSelfEvolution: SelfEvolutionSettings = {
  enabled: false,
  routine_trigger_id: null,
  routine_token_configured: false,
  github_repo: null,
  production_base_url: null,
  daily_caps: CAPS,
  usage_last_24h: NO_USAGE,
  scope_fence: FENCE,
  recent_actions: [],
};

export const activeSelfEvolution: SelfEvolutionSettings = {
  ...unconfiguredSelfEvolution,
  enabled: true,
  routine_trigger_id: 'trig_01ABCdef',
  routine_token_configured: true,
  github_repo: 'acme/janus',
  production_base_url: 'https://janus.example.org',
  usage_last_24h: { ...NO_USAGE, issue_filed: 2, job_started: 1, feature_merged: 1 },
  recent_actions: [
    {
      id: 4,
      action: 'feature_merged',
      status: 'ok',
      subject: '#512',
      detail: 'https://github.com/acme/janus/pull/512',
      data: { kind: 'job' },
      created_at: '2026-10-08T09:12:00+00:00',
    },
    {
      id: 3,
      action: 'feature_merged',
      status: 'refused',
      subject: '#511',
      detail: 'refused: pull request #511 cannot be merged: CI failed: backend-quality',
      data: {},
      created_at: '2026-10-08T08:40:00+00:00',
    },
    {
      id: 2,
      action: 'job_started',
      status: 'ok',
      subject: 'JAN-330',
      detail: 'https://claude.ai/code/session_01ExampleSession',
      data: {},
      created_at: '2026-10-08T07:05:00+00:00',
    },
    {
      id: 1,
      action: 'issue_filed',
      status: 'ok',
      subject: 'JAN-330',
      detail: 'https://linear.app/acme/issue/JAN-330',
      data: {},
      created_at: '2026-10-08T07:01:00+00:00',
    },
  ],
};
