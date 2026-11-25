import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { withQueryData } from '../../test/storybookQueryClient';
import type { TelegramStatus } from '../../telegramApi';
import { TelegramConfig } from './TelegramConfig';

const seed = (data: TelegramStatus) => (queryClient: QueryClient) => {
  queryClient.setQueryData(['telegram'], data);
};

const meta = { title: 'Features/TelegramConfig', component: TelegramConfig } satisfies Meta<typeof TelegramConfig>;

export default meta;

type Story = StoryObj<typeof meta>;

/** No bot yet: the token form. */
export const NotConnected: Story = { decorators: [withQueryData(seed({ connected: false, linked: false }))] };

/** Connected, waiting for the /start message. */
export const WaitingForPairing: Story = {
  decorators: [withQueryData(seed({ connected: true, linked: false, bot_username: 'janus_home_bot', pairing_code: '48151623' }))],
};

/** Linked to the user's chat. */
export const Linked: Story = {
  decorators: [withQueryData(seed({ connected: true, linked: true, bot_username: 'janus_home_bot' }))],
};

/** Linked, but the last poll failed. */
export const WithError: Story = {
  decorators: [
    withQueryData(seed({ connected: true, linked: true, bot_username: 'janus_home_bot', last_error: 'Telegram did not answer in time' })),
  ],
};
