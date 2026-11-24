import type { Meta, StoryObj } from '@storybook/react';
import { expect, userEvent, within } from 'storybook/test';
import { DailyBarChart, VIZ_STYLE, type DailySeries } from './DailyBarChart';
import { makeStatsSample } from './statsFixtures';

type MailKey = 'important' | 'digest' | 'ignore' | 'spam';

const sample = makeStatsSample();
const mailSeries: DailySeries<MailKey>[] = [
  { key: 'important', label: 'Important', color: 'var(--viz-1)' },
  { key: 'digest', label: 'Digest', color: 'var(--viz-2)' },
  { key: 'ignore', label: 'Ignore', color: 'var(--viz-3)' },
  { key: 'spam', label: 'Spam', color: 'var(--viz-4)' },
];

const meta = {
  title: 'Features/Stats/DailyBarChart',
  component: DailyBarChart<MailKey>,
  decorators: [
    (Story) => (
      <div className="stats-viz max-w-3xl p-4">
        <style>{VIZ_STYLE}</style>
        <Story />
      </div>
    ),
  ],
  args: {
    title: 'New mail per day',
    data: sample.mail.per_day,
    series: mailSeries,
    summary: 'Mail per day over 30 days.',
  },
} satisfies Meta<typeof DailyBarChart<MailKey>>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Four stacked series over 30 days, with a legend. */
export const Stacked: Story = {};

/** One series: no legend box, the title names it. */
export const SingleSeries: Story = {
  args: {
    title: 'New memory facts per day',
    data: sample.memory.per_day as never,
    series: [{ key: 'new_facts', label: 'New memory facts', color: 'var(--viz-1)' }] as never,
    height: 120,
  },
};

/** A quiet week: every day is zero, so only the axis and baseline render. */
export const Empty: Story = {
  args: {
    data: sample.mail.per_day.slice(-7).map((row) => ({ ...row, important: 0, digest: 0, ignore: 0, spam: 0 })),
  },
};

/** The same figures as a table, for screen readers and exact values. */
export const TableView: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole('button', { name: 'Show table' }));
    await expect(canvas.getByRole('table')).toBeInTheDocument();
  },
};

/** Hovering a day shows every series' value in a tooltip. */
export const Hovered: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.hover(canvas.getAllByTestId('daily-chart-hit')[12]);
    await expect(canvas.getByRole('tooltip')).toBeInTheDocument();
  },
};
