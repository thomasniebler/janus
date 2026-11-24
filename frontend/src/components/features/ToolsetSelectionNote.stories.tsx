import type { Meta, StoryObj } from '@storybook/react';
import { expect, within } from 'storybook/test';

import { ToolsetSelectionNote } from './ToolsetSelectionNote';

const meta: Meta<typeof ToolsetSelectionNote> = {
  title: 'Features/ToolsetSelectionNote',
  component: ToolsetSelectionNote,
  args: {
    toolset: {
      source: 'selected',
      reason: 'The user asks about tracked topics.',
      tool_ids: ['core', 'topics', 'docs'],
      trimmed: [],
      widened: [],
      authorized_count: 21,
      narrowed: true,
    },
  },
  decorators: [
    (Story) => (
      <div className="max-w-md">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof ToolsetSelectionNote>;

export const Selected: Story = {
  play: async ({ canvasElement }) => {
    const note = within(canvasElement).getByRole('note', { name: 'Tool selection' });
    await expect(note).toHaveTextContent('Tools picked for this message (3 of 21): core, topics, docs');
    await expect(note).toHaveTextContent('Why: The user asks about tracked topics.');
  },
};

export const WidenedAndTrimmed: Story = {
  args: {
    toolset: {
      source: 'selected',
      reason: 'Calendar and memory questions.',
      tool_ids: ['core', 'calendar', 'memory', 'docs'],
      trimmed: ['mail.send'],
      widened: ['memory'],
      authorized_count: 21,
      narrowed: true,
    },
  },
  play: async ({ canvasElement }) => {
    const note = within(canvasElement).getByRole('note', { name: 'Tool selection' });
    await expect(note).toHaveTextContent('Added on request: memory');
    await expect(note).toHaveTextContent('Not available here, dropped: mail.send');
  },
};

/** A fallback offers every authorized tool, so nothing is shown. */
export const Fallback: Story = {
  args: {
    toolset: {
      source: 'fallback',
      reason: 'Tool selection failed (RuntimeError).',
      tool_ids: null,
      trimmed: [],
      widened: [],
      authorized_count: 21,
      narrowed: false,
    },
  },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).queryByRole('note')).toBeNull();
  },
};
