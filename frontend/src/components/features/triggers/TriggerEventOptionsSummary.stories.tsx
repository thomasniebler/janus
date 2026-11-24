import type { Meta, StoryObj } from '@storybook/react';
import type { TriggerDefinition } from '../../../types';
import { TriggerEventOptionsSummary } from './TriggerEventFilterEditor';
import { triggerDefinitionSample } from './triggerFixtures';

/** The fixture's filtered, rate-limited WhatsApp group trigger. */
const familyGroup = triggerDefinitionSample.find((definition) => definition.id === 6) as TriggerDefinition;

const meta = {
  title: 'Features/Runtime triggers/TriggerEventOptionsSummary',
  component: TriggerEventOptionsSummary,
  args: { definition: familyGroup },
  render: (args) => (
    <div className="max-w-xl">
      <TriggerEventOptionsSummary {...args} />
    </div>
  ),
} satisfies Meta<typeof TriggerEventOptionsSummary>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Filter, limit and the count of events the limit dropped. */
export const FilterLimitAndRateLimited: Story = {};

/** A filter with several conditions and a limit, nothing dropped yet. */
export const FilterAndLimit: Story = {
  args: {
    definition: {
      ...familyGroup,
      event_filter: [
        { field: 'chat', op: 'in', values: ['Family, friends', 'Climbing club'] },
        { field: 'body', op: 'contains', values: ['dinner'] },
      ],
      max_runs_per_hour: 1,
      rate_limited_count: 0,
      last_rate_limited_at: null,
    },
  },
};

/** Only the rate-limit drops: no filter, a limit that has been hit. */
export const RateLimitedOnly: Story = {
  args: {
    definition: { ...familyGroup, event_filter: null, max_runs_per_hour: null, rate_limited_count: 1 },
  },
};

/** No filter, no limit, nothing dropped: the summary renders nothing. */
export const NothingSet: Story = {
  args: {
    definition: { ...familyGroup, event_filter: null, max_runs_per_hour: null, rate_limited_count: 0, last_rate_limited_at: null },
  },
};
