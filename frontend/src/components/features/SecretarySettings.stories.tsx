import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { emailApi } from '../../api';
import type { SecretarySettingsResponse } from '../../lib/schemas';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { SecretarySettings } from './SecretarySettings';

const seed = (settings: SecretarySettingsResponse) => (queryClient: QueryClient) => {
  queryClient.setQueryData(['secretary', 'settings'], settings);
};

const meta = {
  title: 'Features/SecretarySettings',
  component: SecretarySettings,
} satisfies Meta<typeof SecretarySettings>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Waking hours at their defaults and WhatsApp alerts off. */
export const Defaults: Story = {
  decorators: [
    withQueryData(
      seed({
        secretary_active_start_hour: 8,
        secretary_active_end_hour: 21,
        secretary_whatsapp_enabled: false,
        secretary_whatsapp_number: null,
      }),
    ),
  ],
};

/** Alerts on, with the owner's own number and a narrower window. */
export const AlertsEnabled: Story = {
  decorators: [
    withQueryData(
      seed({
        secretary_active_start_hour: 9,
        secretary_active_end_hour: 18,
        secretary_whatsapp_enabled: true,
        secretary_whatsapp_number: '+4915112345678',
      }),
    ),
  ],
};

/** Alerts switched on but no number entered yet: the channel stays closed until one is saved. */
export const AlertsOnWithoutNumber: Story = {
  decorators: [
    withQueryData(
      seed({
        secretary_active_start_hour: 8,
        secretary_active_end_hour: 21,
        secretary_whatsapp_enabled: true,
        secretary_whatsapp_number: null,
      }),
    ),
  ],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(emailApi, 'getSecretarySettings', () => neverResolving())],
};

export const LoadFailed: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(emailApi, 'getSecretarySettings', () => Promise.reject(new Error('Network error'))),
  ],
};
