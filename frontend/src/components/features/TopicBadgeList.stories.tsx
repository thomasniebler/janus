import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { TopicBadgeList } from './TopicBadgeList';
import type { EmailTopicBadge } from '../../types';

const topics: EmailTopicBadge[] = [
  { id: 1, title: 'Hiring pipeline', status: 'active', pinned: true },
  { id: 2, title: 'Q3 roadmap', status: 'active', pinned: false },
  { id: 3, title: 'Vendor contract renewal', status: 'snoozed', pinned: false },
  { id: 4, title: 'Possible new project', status: 'candidate', pinned: false },
  { id: 5, title: 'Old migration', status: 'resolved', pinned: false },
];

const meta = {
  title: 'Features/TopicBadgeList',
  component: TopicBadgeList,
  args: { topics },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof TopicBadgeList>;

export default meta;

type Story = StoryObj<typeof meta>;

export const AllStatuses: Story = {};

export const PinnedOnly: Story = {
  args: { topics: [topics[0]] },
};

export const CandidateWatching: Story = {
  args: { topics: [topics[3]] },
};

export const Empty: Story = {
  args: { topics: [] },
};
