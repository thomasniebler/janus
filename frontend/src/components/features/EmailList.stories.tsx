import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { withQueryData } from '../../test/storybookQueryClient';
import { EmailList } from './EmailList';
import type { Email } from '../../types';

const emails: Email[] = [
  {
    id: 1,
    message_id: '<1@example.com>',
    sender: 'alice@example.com',
    subject: 'Hiring update: candidate feedback ready',
    snippet: 'Panel feedback for the senior engineer role is ready for review.',
    priority: 'important',
    classification_confidence: 'high',
    received_at: '2026-09-25T08:00:00Z',
    account_id: 1,
    topics: [{ id: 11, title: 'Hiring pipeline', status: 'active', pinned: true }],
  },
  {
    id: 2,
    message_id: '<2@example.com>',
    sender: 'newsletter@stripe.com',
    subject: 'Your weekly Stripe digest',
    snippet: 'A roundup of payments activity from the last seven days.',
    priority: 'digest',
    classification_confidence: 'medium',
    received_at: '2026-09-24T14:30:00Z',
    account_id: 1,
    topics: [],
  },
  {
    id: 3,
    message_id: '<3@example.com>',
    sender: 'winner@totally-legit-prize.example',
    subject: 'YOU HAVE WON A FREE PRIZE!!!',
    snippet: 'Click here immediately to claim your reward before it expires.',
    priority: 'spam',
    classification_confidence: 'high',
    received_at: '2026-09-23T09:15:00Z',
    account_id: 1,
    topics: [],
  },
  {
    id: 4,
    message_id: '<4@example.com>',
    sender: 'noreply@github.com',
    subject: '[janus] New workflow run completed',
    snippet: 'The scheduled CI run finished successfully.',
    priority: 'ignore',
    classification_confidence: null,
    received_at: '2026-09-22T06:00:00Z',
    account_id: 1,
    topics: [],
  },
];

const meta = {
  title: 'Features/EmailList',
  component: EmailList,
  args: {
    emails,
    isLoading: false,
    onOpenEmail: () => {},
  },
  decorators: [withQueryData()],
  render: (args) => (
    <MemoryRouter>
      <EmailList {...args} />
    </MemoryRouter>
  ),
} satisfies Meta<typeof EmailList>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Populated: Story = {};

export const Loading: Story = {
  args: { isLoading: true, emails: [] },
};

export const Empty: Story = {
  args: { emails: [] },
};

export const SingleEmail: Story = {
  args: { emails: [emails[0]] },
};
