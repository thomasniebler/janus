import type { Meta, StoryObj } from '@storybook/react';
import { LlmUsageCard } from './LlmUsageCard';
import { makeStatsSample } from './statsFixtures';

const usage = makeStatsSample(14).llm;

const meta = { title: 'Features/Stats/LlmUsageCard', component: LlmUsageCard } satisfies Meta<typeof LlmUsageCard>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Chat, trigger runs and delegated tasks on two models. */
export const Populated: Story = { args: { usage, days: 14 } };

/** Nothing was called in the range. */
export const Empty: Story = {
  args: {
    days: 7,
    usage: {
      per_day: usage.per_day.slice(0, 7).map((day) => ({ ...day, calls: 0, input_tokens: 0, output_tokens: 0 })),
      by_surface: [],
      by_model: [],
      totals: { calls: 0, input_tokens: 0, output_tokens: 0, unreported_calls: 0 },
    },
  },
};

/** Streamed chat on a proxy that reports no usage. */
export const UnreportedCalls: Story = {
  args: { days: 14, usage: { ...usage, totals: { ...usage.totals, unreported_calls: 12 } } },
};
