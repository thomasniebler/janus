import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { TriggerEventPicker } from './TriggerEventPicker';
import { triggerEventCatalogSample } from './triggerFixtures';

const meta = {
  title: 'Features/Runtime triggers/TriggerEventPicker',
  component: TriggerEventPicker,
  args: {
    catalog: triggerEventCatalogSample,
    value: 'mail.received',
    onChange: () => undefined,
  },
  render: function Render(args) {
    const [value, setValue] = useState(args.value);
    return (
      <div className="grid gap-4 md:grid-cols-2 max-w-2xl">
        <TriggerEventPicker
          {...args}
          value={value}
          onChange={(next) => {
            setValue(next);
            args.onChange(next);
          }}
        />
      </div>
    );
  },
} satisfies Meta<typeof TriggerEventPicker>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Selected: Story = {};

export const NothingChosenYet: Story = {
  args: { value: null },
};

/** A stored value the backend no longer publishes: flagged until a real event is picked. */
export const UncataloguedValue: Story = {
  args: { value: 'calendar.changed' },
};

export const CatalogLoading: Story = {
  args: { catalog: [], value: null },
};
