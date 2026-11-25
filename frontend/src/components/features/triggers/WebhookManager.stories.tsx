import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { neverResolving, withApiOverride, withQueryData } from '../../../test/storybookQueryClient';
import type { Webhook } from '../../../types';
import { webhooksApi } from '../../../webhooksApi';
import { WebhookManager } from './WebhookManager';

const webhooks: Webhook[] = [
  { id: 1, name: 'GitHub pushes', key_prefix: 'Xq3vP9aL', enabled: true, received_count: 42, last_received_at: '2026-11-25T09:30:00Z', created_at: '2026-11-01T00:00:00Z' },
  { id: 2, name: 'Home server alerts', key_prefix: 'k2Ld8sQe', enabled: false, received_count: 0, last_received_at: null, created_at: '2026-11-20T00:00:00Z' },
];

const seed = (data: Webhook[]) => (queryClient: QueryClient) => {
  queryClient.setQueryData(['webhooks'], data);
};

const meta = { title: 'Features/Triggers/WebhookManager', component: WebhookManager } satisfies Meta<typeof WebhookManager>;

export default meta;

type Story = StoryObj<typeof meta>;

/** One active webhook with deliveries, one switched off. */
export const Populated: Story = { decorators: [withQueryData(seed(webhooks))] };

export const Empty: Story = { decorators: [withQueryData(seed([]))] };

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(webhooksApi, 'list', () => neverResolving())],
};

export const Unavailable: Story = {
  decorators: [withQueryData(), withApiOverride(webhooksApi, 'list', () => Promise.reject(new Error('unavailable')))],
};
