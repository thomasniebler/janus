import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { withQueryData } from '../../test/storybookQueryClient';
import { BriefingTab } from './BriefingTab';

const seed = (queryClient: QueryClient) => {
  queryClient.setQueryData(
    ['dashboard', 'widgets'],
    [
      {
        widget_key: 'briefing',
        is_visible: true,
        is_collapsed: false,
        prompt_override: null,
        briefing_email_window: null,
      },
    ],
  );
  queryClient.setQueryData(['secretary', 'settings'], {
    secretary_active_start_hour: 8,
    secretary_active_end_hour: 21,
    secretary_whatsapp_enabled: false,
    secretary_whatsapp_number: null,
  });
};

const meta = {
  title: 'Features/BriefingTab',
  component: BriefingTab,
} satisfies Meta<typeof BriefingTab>;

export default meta;

type Story = StoryObj<typeof meta>;

/** The Daily Briefing settings tab: the briefing card with the Secretary card below it. */
export const BriefingAndSecretary: Story = {
  decorators: [withQueryData(seed)],
};
