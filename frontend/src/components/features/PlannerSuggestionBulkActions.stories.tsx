import type { Meta, StoryObj } from '@storybook/react';
import { fn } from 'storybook/test';
import { withQueryData } from '../../test/storybookQueryClient';
import type { PlannerSuggestion } from '../../types';
import { PlannerSuggestionBulkActions } from './PlannerSuggestionBulkActions';

const suggestion = (id: number, title: string): PlannerSuggestion => ({
  id,
  suggestion_type: 'task',
  status: 'pending',
  source: 'email_agent',
  all_day: false,
  title,
  created_at: '2026-11-20T08:00:00Z',
  updated_at: '2026-11-20T08:00:00Z',
});

const suggestions = [
  suggestion(1, 'Finish board deck'),
  suggestion(2, 'finish the board deck!'),
  suggestion(3, 'Book flights to Lisbon'),
];

/** Seeds the duplicate count the "Dismiss duplicates" button shows (keyed by how many are listed). */
const withDuplicates = (count: number, listed = suggestions.length) =>
  withQueryData((queryClient) => {
    queryClient.setQueryData(['planner', 'suggestions', 'duplicates', listed], {
      groups: count ? 1 : 0,
      duplicates: count,
    });
  });

const meta = {
  title: 'Features/Planner/PlannerSuggestionBulkActions',
  component: PlannerSuggestionBulkActions,
  args: { suggestions, selectedIds: [], onSelectionChange: fn(), onDismissed: fn() },
  decorators: [withDuplicates(1)],
} satisfies Meta<typeof PlannerSuggestionBulkActions>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Nothing selected: Dismiss selected is disabled, one duplicate can be collapsed. */
export const NothingSelected: Story = {};

/** Some suggestions ticked: Dismiss selected counts them. */
export const SomeSelected: Story = { args: { selectedIds: [1, 3] } };

/** Every suggestion ticked: Select all is checked. */
export const AllSelected: Story = { args: { selectedIds: [1, 2, 3] } };

/** No copies among the pending suggestions: Dismiss duplicates is disabled. */
export const NoDuplicates: Story = { decorators: [withDuplicates(0)] };

/** A single suggestion: the duplicate count is not even asked for. */
export const SingleSuggestion: Story = {
  args: { suggestions: [suggestion(4, 'Renew passport')] },
  decorators: [withDuplicates(0, 1)],
};
