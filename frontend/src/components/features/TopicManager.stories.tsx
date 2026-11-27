import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { topicApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { TopicManager } from './TopicManager';
import type { TopicDetail, TopicSummary } from '../../types';

const hiringPipeline: TopicSummary = {
  id: 1,
  title: 'Hiring pipeline',
  summary: 'Panel feedback is in for the senior engineer role; offer decision pending.',
  marker_keywords: ['hiring', 'candidate', 'offer'],
  status: 'active',
  pinned: true,
  user_confirmed: true,
  origin: 'auto',
  confidence: 'high',
  first_seen_at: '2026-09-01T08:00:00Z',
  last_seen_at: '2026-09-25T09:00:00Z',
  snoozed_until: null,
  last_briefed_at: '2026-09-25T07:00:00Z',
  last_digested_at: null,
  created_at: '2026-09-01T08:00:00Z',
  updated_at: '2026-09-25T09:00:00Z',
  evidence_count: 6,
};

const homeBlog: TopicSummary = {
  id: 2,
  title: 'Home blog',
  summary: 'Personal blog redesign, hosting, and writing schedule.',
  marker_keywords: ['blog', 'hosting'],
  status: 'active',
  pinned: false,
  user_confirmed: true,
  origin: 'manual',
  confidence: null,
  first_seen_at: '2026-08-01T08:00:00Z',
  last_seen_at: '2026-09-20T08:00:00Z',
  snoozed_until: null,
  last_briefed_at: null,
  last_digested_at: null,
  created_at: '2026-08-01T08:00:00Z',
  updated_at: '2026-09-20T08:00:00Z',
  evidence_count: 2,
};

const watchingTopic: TopicSummary = {
  ...hiringPipeline,
  id: 3,
  title: 'Possible new client project',
  status: 'candidate',
  pinned: false,
  confidence: 'low',
  evidence_count: 1,
};

const topics: TopicSummary[] = [hiringPipeline, homeBlog];

const hiringDetail: TopicDetail = {
  ...hiringPipeline,
  evidence: [
    {
      id: 1,
      source_type: 'email',
      excerpt: 'Panel feedback for the senior engineer role is ready for review.',
      evidence_role: 'trigger',
      created_at: '2026-09-25T08:00:00Z',
    },
    {
      id: 2,
      source_type: 'email',
      excerpt: 'Following up on the offer decision timeline.',
      evidence_role: 'supporting',
      created_at: '2026-09-24T08:00:00Z',
    },
  ],
};

const manualDetail: TopicDetail = { ...homeBlog, evidence: [] };

const seed =
  (options: {
    topics?: TopicSummary[];
    selected?: { id: number; detail: TopicDetail };
  }) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['topics', '', 'active'], options.topics ?? topics);
    if (options.selected) {
      queryClient.setQueryData(['topic-detail', options.selected.id], options.selected.detail);
    }
  };

const meta = {
  title: 'Features/TopicManager',
  component: TopicManager,
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof TopicManager>;

export default meta;

type Story = StoryObj<typeof meta>;

export const ActiveTopics: Story = {
  decorators: [withQueryData(seed({}))],
};

export const NoTopicsYet: Story = {
  decorators: [withQueryData(seed({ topics: [] }))],
};

export const WatchingCandidateTopic: Story = {
  decorators: [withQueryData(seed({ topics: [watchingTopic] }))],
};

export const SelectedAutoTopic: Story = {
  decorators: [
    withQueryData(seed({ selected: { id: 1, detail: hiringDetail } })),
    (Story) => (
      <MemoryRouter initialEntries={['/memory?tab=topics&topic=1']}>
        <Story />
      </MemoryRouter>
    ),
  ],
};

export const SelectedManualTopicEditable: Story = {
  decorators: [
    withQueryData(seed({ selected: { id: 2, detail: manualDetail } })),
    (Story) => (
      <MemoryRouter initialEntries={['/memory?tab=topics&topic=2']}>
        <Story />
      </MemoryRouter>
    ),
  ],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(topicApi, 'list', () => neverResolving())],
};
