import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { withQueryData } from '../../test/storybookQueryClient';
import { EmailItem } from './EmailItem';
import type { Email } from '../../types';

const baseEmail: Email = {
  id: 7,
  message_id: '<7@example.com>',
  sender: 'alice@example.com',
  subject: 'Hiring update: candidate feedback ready',
  snippet: 'Hey, I put together the panel feedback for the senior engineer role — take a look when you get a chance.',
  priority: 'important',
  classification_confidence: 'high',
  received_at: '2026-09-25T08:00:00Z',
  account_id: 1,
  topics: [{ id: 11, title: 'Hiring pipeline', status: 'active', pinned: true }],
};

const meta = {
  title: 'Features/EmailItem',
  component: EmailItem,
  args: {
    email: baseEmail,
    onOpenEmail: () => {},
  },
  decorators: [withQueryData()],
  render: (args) => (
    <MemoryRouter>
      <EmailItem {...args} />
    </MemoryRouter>
  ),
} satisfies Meta<typeof EmailItem>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Important: Story = {};

export const Digest: Story = {
  args: {
    email: {
      ...baseEmail,
      id: 8,
      sender: 'newsletter@stripe.com',
      subject: 'Your weekly Stripe digest',
      snippet: 'Here is a roundup of payments activity from the last seven days.',
      priority: 'digest',
      classification_confidence: 'medium',
      topics: [],
    },
  },
};

export const Spam: Story = {
  args: {
    email: {
      ...baseEmail,
      id: 9,
      sender: 'winner@totally-legit-prize.example',
      subject: 'YOU HAVE WON A FREE PRIZE!!!',
      snippet: 'Click here immediately to claim your reward before it expires.',
      priority: 'spam',
      classification_confidence: 'high',
      topics: [],
    },
  },
};

export const Ignored: Story = {
  args: {
    email: {
      ...baseEmail,
      id: 10,
      sender: 'noreply@github.com',
      subject: '[janus] New workflow run completed',
      snippet: 'The scheduled CI run finished successfully.',
      priority: 'ignore',
      classification_confidence: null,
      topics: [],
    },
  },
};

export const LowConfidence: Story = {
  args: {
    email: {
      ...baseEmail,
      id: 11,
      classification_confidence: 'low',
    },
  },
};

export const NoTopics: Story = {
  args: {
    email: {
      ...baseEmail,
      id: 12,
      topics: [],
    },
  },
};
