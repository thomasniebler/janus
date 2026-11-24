import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { withQueryData } from '../../test/storybookQueryClient';
import { EmailDetailView } from './EmailDetailView';
import type { Email } from '../../types';

const email: Email = {
  id: 7,
  message_id: '<7@example.com>',
  sender: 'alice@example.com',
  subject: 'Hiring update: candidate feedback ready',
  snippet:
    'Hey, I put together the panel feedback for the senior engineer role. Everyone rated the candidate ' +
    'highly on system design, and a couple of people had minor concerns about communication style. ' +
    'Let me know if you want to sync before we send the offer.',
  priority: 'important',
  classification_confidence: 'high',
  reasoning: 'Sender is a known colleague and the subject references an active hiring topic Janus is tracking.',
  received_at: '2026-09-25T08:00:00Z',
  account_id: 1,
  topics: [{ id: 11, title: 'Hiring pipeline', status: 'active', pinned: true }],
};

const meta = {
  title: 'Features/EmailDetailView',
  component: EmailDetailView,
  args: {
    email,
    onClose: () => {},
  },
  decorators: [withQueryData()],
  render: (args) => (
    <MemoryRouter>
      <EmailDetailView {...args} />
    </MemoryRouter>
  ),
} satisfies Meta<typeof EmailDetailView>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const NoTopicsOrReasoning: Story = {
  args: {
    email: {
      ...email,
      id: 8,
      reasoning: null,
      classification_confidence: null,
      topics: [],
    },
  },
};

export const Spam: Story = {
  args: {
    email: {
      ...email,
      id: 9,
      sender: 'winner@totally-legit-prize.example',
      subject: 'YOU HAVE WON A FREE PRIZE!!!',
      priority: 'spam',
      reasoning: 'Contains urgency language and an unrecognized sender domain typical of prize-scam spam.',
      topics: [],
    },
  },
};
