import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { emailApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { EmailClarificationQueue } from './EmailClarificationQueue';
import type { EmailClarificationRequest } from '../../types';

const clarifications: EmailClarificationRequest[] = [
  {
    id: 1,
    email_id: 101,
    applied_rule_id: null,
    previous_priority: 'digest',
    requested_priority: 'important',
    suggested_rule_name: 'Recruiter emails are important',
    suggested_rule_pattern: 'recruiting@.*\\.example\\.com',
    suggested_rule_field: 'sender',
    suggested_rule_priority: 'important',
    suggestion_confidence: 'high',
    suggestion_reason: 'You marked this recruiter email as important twice this week.',
    status: 'pending',
    created_at: '2026-09-25T08:00:00Z',
    updated_at: '2026-09-25T08:00:00Z',
    email: {
      id: 101,
      sender: 'recruiting@bigco.example.com',
      subject: 'Following up on your application',
      snippet: 'We would love to schedule a call this week.',
      priority: 'digest',
      classification_confidence: 'medium',
    },
  },
  {
    id: 2,
    email_id: 102,
    applied_rule_id: null,
    previous_priority: 'important',
    requested_priority: 'ignore',
    suggested_rule_name: 'Ignore CI notification noise',
    suggested_rule_pattern: 'noreply@github\\.com',
    suggested_rule_field: 'sender',
    suggested_rule_priority: 'ignore',
    suggestion_confidence: 'low',
    suggestion_reason: 'You dismissed a similar CI notification yesterday.',
    status: 'pending',
    created_at: '2026-09-24T11:00:00Z',
    updated_at: '2026-09-24T11:00:00Z',
    email: {
      id: 102,
      sender: 'noreply@github.com',
      subject: '[janus] Workflow run failed',
      snippet: 'The nightly build failed on main.',
      priority: 'important',
      classification_confidence: 'low',
    },
  },
];

const seed =
  (data: EmailClarificationRequest[]) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['email-clarifications'], data);
  };

const meta = {
  title: 'Features/EmailClarificationQueue',
  component: EmailClarificationQueue,
} satisfies Meta<typeof EmailClarificationQueue>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Pending: Story = {
  decorators: [withQueryData(seed(clarifications))],
};

export const Empty: Story = {
  decorators: [withQueryData(seed([]))],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(emailApi, 'listClarifications', () => neverResolving())],
};
