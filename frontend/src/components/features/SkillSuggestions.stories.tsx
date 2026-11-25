import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { fn } from 'storybook/test';
import type { SkillSuggestion } from '../../skillSuggestionsApi';
import { withQueryData } from '../../test/storybookQueryClient';
import { SkillSuggestions } from './SkillSuggestions';

const suggestions: SkillSuggestion[] = [
  {
    key: 'a',
    tools: ['search_mail', 'read_email', 'create_task'],
    turns: 5,
    examples: ['Invoices from ACME', 'Travel receipts', 'Contract renewal'],
    brief: 'In recent chats Janus repeatedly used the tools search_mail -> read_email -> create_task ...',
  },
  { key: 'b', tools: ['list_events', 'draft_reply'], turns: 3, examples: ['Meeting reschedule'], brief: '...' },
];

const seed = (data: SkillSuggestion[]) => (queryClient: QueryClient) => {
  queryClient.setQueryData(['skill-suggestions'], data);
};

const meta = {
  title: 'Features/SkillSuggestions',
  component: SkillSuggestions,
  args: { onDraft: fn() },
} satisfies Meta<typeof SkillSuggestions>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Two routines Janus noticed. */
export const WithSuggestions: Story = { decorators: [withQueryData(seed(suggestions))] };

/** No routine repeated often enough: the card is not shown at all. */
export const NoSuggestions: Story = { decorators: [withQueryData(seed([]))] };
