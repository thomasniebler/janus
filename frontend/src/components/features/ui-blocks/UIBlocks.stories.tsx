import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, fn, userEvent, within } from 'storybook/test';

import { UIBlockList, UIBlockView } from './UIBlockView';
import {
  actionsBlock,
  barChartBlock,
  cardBlock,
  checklistBlock,
  imageBlock,
  invalidBlocks,
  keyValueBlock,
  lineChartBlock,
  pieChartBlock,
  tableBlock,
} from './uiBlockFixtures';

/** The chat UI-block catalog (JAN-252), one story per component plus the invalid-payload fallback. */
const meta = {
  title: 'Features/UIBlocks',
  component: UIBlockView,
  decorators: [
    (Story) => (
      <MemoryRouter>
        <div className="max-w-md p-4">
          <Story />
        </div>
      </MemoryRouter>
    ),
  ],
  args: { onAction: fn(() => true) },
} satisfies Meta<typeof UIBlockView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Card: Story = {
  args: { block: cardBlock },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText('Payment of <b>120 EUR</b> was due on Friday.')).toBeInTheDocument();
    await expect(canvas.getByRole('link', { name: /Pay online/ })).toHaveAttribute('rel', 'noopener noreferrer');
  },
};

export const Table: Story = { args: { block: tableBlock } };

export const BarChart: Story = { args: { block: barChartBlock } };

export const LineChart: Story = { args: { block: lineChartBlock } };

export const PieChart: Story = { args: { block: pieChartBlock } };

export const Checklist: Story = { args: { block: checklistBlock } };

export const KeyValue: Story = { args: { block: keyValueBlock } };

export const Actions: Story = {
  args: { block: actionsBlock },
  play: async ({ args, canvasElement }) => {
    const button = within(canvasElement).getByRole('button', { name: 'Snooze' });
    await userEvent.click(button);
    await expect(args.onAction).toHaveBeenCalledWith('Run "Snooze": call snooze_topic with {"topic_id":4}');
    await expect(button).toBeDisabled();
  },
};

/** `/api/images/<id>` does not exist in Storybook, so this also shows the load-failure placeholder. */
export const Image: Story = { args: { block: imageBlock } };

export const ImageWithoutUrl: Story = { args: { block: { ...imageBlock, url: undefined } } };

export const InvalidFallback: Story = {
  args: { block: invalidBlocks.unknownType },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText('Could not render this block')).toBeInTheDocument();
  },
};

export const ExternalImageRefused: Story = { args: { block: invalidBlocks.externalImage } };

/** A whole reply's blocks, one of them invalid: the others still render. */
export const ReplyWithSeveralBlocks: Story = {
  args: { block: cardBlock },
  render: (args) => (
    <UIBlockList blocks={[cardBlock, tableBlock, invalidBlocks.futureVersion, actionsBlock]} onAction={args.onAction} />
  ),
};
