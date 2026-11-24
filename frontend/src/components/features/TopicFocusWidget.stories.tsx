import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { TopicFocusWidget } from './TopicFocusWidget';
import type { TopicSummary } from '../../types';

const topics: TopicSummary[] = [
  {
    id: 1,
    title: 'Hiring pipeline',
    summary: 'Panel feedback is in for the senior engineer role; offer decision pending.',
    marker_keywords: ['hiring', 'candidate'],
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
  },
  {
    id: 2,
    title: 'Q3 roadmap review',
    summary: null,
    marker_keywords: ['roadmap', 'planning'],
    status: 'active',
    pinned: false,
    user_confirmed: false,
    origin: 'auto',
    confidence: 'medium',
    first_seen_at: '2026-09-10T08:00:00Z',
    last_seen_at: '2026-09-24T14:00:00Z',
    snoozed_until: null,
    last_briefed_at: null,
    last_digested_at: null,
    created_at: '2026-09-10T08:00:00Z',
    updated_at: '2026-09-24T14:00:00Z',
    evidence_count: 3,
  },
  {
    id: 3,
    title: 'Vendor contract renewal',
    summary: 'Waiting on legal sign-off before the renewal deadline.',
    marker_keywords: ['contract', 'legal'],
    status: 'snoozed',
    pinned: false,
    user_confirmed: true,
    origin: 'manual',
    confidence: 'low',
    first_seen_at: '2026-08-15T08:00:00Z',
    last_seen_at: '2026-09-18T11:00:00Z',
    snoozed_until: '2026-10-01T00:00:00Z',
    last_briefed_at: null,
    last_digested_at: '2026-09-18T11:00:00Z',
    created_at: '2026-08-15T08:00:00Z',
    updated_at: '2026-09-18T11:00:00Z',
    evidence_count: 2,
  },
];

const meta = {
  title: 'Features/TopicFocusWidget',
  component: TopicFocusWidget,
  args: { topics },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof TopicFocusWidget>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Populated: Story = {};

export const Empty: Story = {
  args: { topics: [] },
};

export const SingleTopic: Story = {
  args: { topics: [topics[0]] },
};
