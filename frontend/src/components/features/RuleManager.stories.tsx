import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { emailApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { RuleManager } from './RuleManager';
import type { Rule } from '../../types';

const rules: Rule[] = [
  { id: 1, name: 'Work Urgent', pattern: 'boss@example.com', field: 'sender', priority: 'important', is_active: true, order: 0 },
  { id: 2, name: 'Newsletters', pattern: 'newsletter@', field: 'sender', priority: 'digest', is_active: true, order: 1 },
  { id: 3, name: 'CI noise', pattern: 'noreply@github.com', field: 'sender', priority: 'ignore', is_active: true, order: 2 },
  { id: 4, name: 'Prize scams', pattern: 'won a free prize', field: 'subject', priority: 'spam', is_active: true, order: 3 },
];

const seed =
  (data: Rule[]) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['email-rules'], data);
  };

const meta = {
  title: 'Features/RuleManager',
  component: RuleManager,
} satisfies Meta<typeof RuleManager>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Populated: Story = {
  decorators: [withQueryData(seed(rules))],
};

export const Empty: Story = {
  decorators: [withQueryData(seed([]))],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(emailApi, 'getRules', () => neverResolving())],
};
