import type { Meta, StoryObj } from '@storybook/react';
import { withQueryData } from '../../test/storybookQueryClient';
import { EmailPriorityActions } from './EmailPriorityActions';
import type { Email } from '../../types';

const email: Email = {
  id: 7,
  message_id: '<7@example.com>',
  sender: 'alice@example.com',
  subject: 'Hiring update',
  snippet: 'Candidate feedback is ready.',
  priority: 'important',
  received_at: '2026-09-25T08:00:00Z',
  account_id: 1,
  topics: [],
};

const meta = {
  title: 'Features/EmailPriorityActions',
  component: EmailPriorityActions,
  args: {
    email,
    size: 'sm',
  },
  decorators: [withQueryData()],
} satisfies Meta<typeof EmailPriorityActions>;

export default meta;

type Story = StoryObj<typeof meta>;

export const MarkedImportant: Story = {};

export const MarkedDigest: Story = {
  args: { email: { ...email, priority: 'digest' } },
};

export const MarkedSpam: Story = {
  args: { email: { ...email, priority: 'spam' } },
};

export const WithLabel: Story = {
  args: { label: 'Classify from list' },
};

export const MediumSize: Story = {
  args: { size: 'md' },
};
