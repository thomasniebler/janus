import type { Meta, StoryObj } from '@storybook/react';
import { makeStatsSample } from '../components/features/stats/statsFixtures';
import { withQueryData } from '../test/storybookQueryClient';
import StatsPage from './StatsPage';

const timezone = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
})();

const meta = {
  title: 'Pages/StatsPage',
  component: StatsPage,
} satisfies Meta<typeof StatsPage>;

export default meta;

type Story = StoryObj<typeof meta>;

export const ThirtyDays: Story = {
  decorators: [withQueryData((queryClient) => queryClient.setQueryData(['stats', 30, timezone], makeStatsSample()))],
};
