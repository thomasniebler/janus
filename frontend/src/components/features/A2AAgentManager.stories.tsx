import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { a2aApi } from '../../a2aApi';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import type { A2AAgent } from '../../types';
import { A2AAgentManager } from './A2AAgentManager';

const base: A2AAgent = {
  id: 1,
  name: 'Research agent',
  base_url: 'https://research.example.com',
  enabled: true,
  card_name: 'Research Agent',
  card_description: 'Finds and summarizes papers.',
  protocol_version: '1.0',
  skills: [
    { name: 'Literature search', description: 'Finds papers on a topic' },
    { name: 'Summaries', description: 'Summarizes a paper' },
  ],
  rpc_url: 'https://research.example.com/a2a/v1',
  token_configured: true,
  last_error: null,
  last_checked_at: '2026-11-25T10:00:00Z',
  created_at: '2026-11-20T00:00:00Z',
  updated_at: '2026-11-25T10:00:00Z',
};

const agents: A2AAgent[] = [
  base,
  { ...base, id: 2, name: 'Support desk', base_url: 'https://support.example.com', enabled: false, token_configured: false, protocol_version: '0.3.0', skills: [] },
  { ...base, id: 3, name: 'Gone agent', base_url: 'https://gone.example.com', enabled: false, card_name: null, protocol_version: null, skills: [], rpc_url: null, token_configured: false, last_error: 'no agent card found (the remote agent returned HTTP 404)' },
];

const seed = (data: A2AAgent[]) => (queryClient: QueryClient) => {
  queryClient.setQueryData(['a2a-agents'], data);
};

const meta = {
  title: 'Features/A2AAgentManager',
  component: A2AAgentManager,
} satisfies Meta<typeof A2AAgentManager>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Switched on, switched off, and one whose card could not be fetched (cannot be switched on). */
export const Populated: Story = { decorators: [withQueryData(seed(agents))] };

export const Empty: Story = { decorators: [withQueryData(seed([]))] };

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(a2aApi, 'list', () => neverResolving())],
};

export const Unavailable: Story = {
  decorators: [withQueryData(), withApiOverride(a2aApi, 'list', () => Promise.reject(new Error('unavailable')))],
};
