import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { dashboardApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { BriefingSettings } from './BriefingSettings';
import type { DashboardWidgetPreference } from '../../types';

const seed =
  (preferences: DashboardWidgetPreference[]) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['dashboard', 'widgets'], preferences);
  };

const meta = {
  title: 'Features/BriefingSettings',
  component: BriefingSettings,
} satisfies Meta<typeof BriefingSettings>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Defaults: Story = {
  decorators: [
    withQueryData(
      seed([
        {
          widget_key: 'briefing',
          is_visible: true,
          is_collapsed: false,
          prompt_override: null,
          briefing_email_window: null,
        },
      ])
    ),
  ],
};

export const CustomizedWithEmailWindow: Story = {
  decorators: [
    withQueryData(
      seed([
        {
          widget_key: 'briefing',
          is_visible: true,
          is_collapsed: false,
          prompt_override: 'Lead with blockers, then highlight important follow-ups.',
          briefing_email_window: 'last_24_hours',
        },
      ])
    ),
  ],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(dashboardApi, 'getWidgets', () => neverResolving())],
};
