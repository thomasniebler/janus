import type { Meta, StoryObj } from '@storybook/react';
import { fn } from 'storybook/test';
import { TriggerRunLimitsFields } from './TriggerRunLimitsFields';

const meta = {
  title: 'Features/Runtime triggers/TriggerRunLimitsFields',
  component: TriggerRunLimitsFields,
  args: { limits: { max_tool_calls: null, max_run_seconds: null }, onChange: fn() },
  render: (args) => (
    <div className="max-w-xl">
      <TriggerRunLimitsFields {...args} />
    </div>
  ),
} satisfies Meta<typeof TriggerRunLimitsFields>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Both limits at Janus's defaults (8 rounds, 180 seconds). */
export const Defaults: Story = {};

/** Custom values: the number inputs appear. */
export const Custom: Story = { args: { limits: { max_tool_calls: 25, max_run_seconds: 600 } } };

/** Both limits off: the warning about serial runs and cost is shown. */
export const NoLimit: Story = { args: { limits: { max_tool_calls: 0, max_run_seconds: 0 } } };

/** Mixed: more rounds, no time limit. */
export const MixedCustomAndOff: Story = { args: { limits: { max_tool_calls: 40, max_run_seconds: 0 } } };
