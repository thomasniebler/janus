import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { mailIngestionApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { MailIngestionConfig } from './MailIngestionConfig';
import type { MailIngestionSettings } from '../../types';

const seed =
  (settings: MailIngestionSettings) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['mail-ingestion-settings'], settings);
  };

const meta = {
  title: 'Features/MailIngestionConfig',
  component: MailIngestionConfig,
  args: { isAdmin: true },
} satisfies Meta<typeof MailIngestionConfig>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {
  decorators: [withQueryData(seed({ initial_sync_email_limit: 100 }))],
};

export const HighLimit: Story = {
  decorators: [withQueryData(seed({ initial_sync_email_limit: 500 }))],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(mailIngestionApi, 'getSettings', () => neverResolving())],
};

/** A non-admin sees the limit but cannot change it (JAN-222). */
export const ReadOnlyForNonAdmin: Story = {
  args: { isAdmin: false },
  decorators: [withQueryData(seed({ initial_sync_email_limit: 100 }))],
};
