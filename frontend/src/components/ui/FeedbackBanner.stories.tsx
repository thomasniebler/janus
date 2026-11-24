import type { Meta, StoryObj } from '@storybook/react';
import { FeedbackBanner } from './FeedbackBanner';

const meta = {
  title: 'UI/FeedbackBanner',
  component: FeedbackBanner,
} satisfies Meta<typeof FeedbackBanner>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Success: Story = {
  args: { feedback: { status: 'success', message: 'GitHub settings saved.' } },
};

export const Failure: Story = {
  args: { feedback: { status: 'error', message: 'Admin privileges required.' } },
};

export const Empty: Story = {
  args: { feedback: null },
};
