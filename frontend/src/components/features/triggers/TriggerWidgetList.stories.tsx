import type { Meta, StoryObj } from '@storybook/react';
import { TriggerWidgetList } from './TriggerWidgetList';
import { makeTrigger, triggerSample } from './triggerFixtures';

const meta = {
  title: 'Features/Runtime triggers/TriggerWidgetList',
  component: TriggerWidgetList,
  args: {
    triggers: triggerSample,
    widgetStyle: 'card',
  },
  argTypes: {
    widgetStyle: { control: 'inline-radio', options: ['row', 'card', 'timeline'] },
  },
} satisfies Meta<typeof TriggerWidgetList>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Card: Story = {
  args: { widgetStyle: 'card' },
};

export const Row: Story = {
  args: { widgetStyle: 'row' },
};

export const Timeline: Story = {
  args: { widgetStyle: 'timeline' },
};

export const Empty: Story = {
  args: { triggers: [] },
};

export const SingleQueuedTrigger: Story = {
  args: { triggers: [makeTrigger('queued', 'scheduled')] },
};
