import type { Meta, StoryObj } from '@storybook/react';
import { SubTabs } from './SubTabs';

const meta = {
  title: 'UI/SubTabs',
  component: SubTabs,
  args: {
    ariaLabel: 'Connections sections',
    idPrefix: 'story',
    activeId: 'mail',
    onSelect: () => undefined,
    tabs: [
      { id: 'mail', name: 'Mail' },
      { id: 'calendar', name: 'Calendar & tasks' },
      { id: 'messaging', name: 'Messaging' },
      { id: 'developer', name: 'Developer' },
    ],
  },
} satisfies Meta<typeof SubTabs>;

export default meta;

type Story = StoryObj<typeof meta>;

export const FirstSelected: Story = {};

export const LastSelected: Story = { args: { activeId: 'developer' } };
