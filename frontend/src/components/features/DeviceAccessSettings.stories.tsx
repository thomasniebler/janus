import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { expect, userEvent, within } from 'storybook/test';
import { inboxApi, type DeviceToken } from '../../lib/inboxApi';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { DeviceAccessSettings } from './DeviceAccessSettings';

const seed = (tokens: DeviceToken[]) => (queryClient: QueryClient) => {
  queryClient.setQueryData(['inbox', 'tokens'], tokens);
};

const meta = {
  title: 'Features/DeviceAccessSettings',
  component: DeviceAccessSettings,
} satisfies Meta<typeof DeviceAccessSettings>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Empty: Story = {
  decorators: [withQueryData(seed([]))],
};

export const WithTokens: Story = {
  decorators: [
    withQueryData(
      seed([
        {
          id: 2,
          name: 'Laptop eww popup',
          token_prefix: 'jdt_Ab3x',
          created_at: '2026-11-20T09:00:00Z',
          last_used_at: '2026-11-23T07:12:00Z',
        },
        { id: 1, name: 'Phone', token_prefix: 'jdt_Q9zk', created_at: '2026-11-18T18:30:00Z', last_used_at: null },
      ]),
    ),
  ],
};

/** Right after creation: the secret is shown once, with a copy button. */
export const JustCreated: Story = {
  decorators: [
    withQueryData(seed([])),
    withApiOverride(inboxApi, 'createToken', async (name: string) => ({
      data: {
        id: 3,
        name,
        token: 'jdt_example-secret-shown-once',
        token_prefix: 'jdt_exam',
        created_at: '2026-11-23T08:00:00Z',
        last_used_at: null,
      },
      status: 201,
      statusText: 'Created',
      headers: {},
      config: {} as never,
    })),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(await canvas.findByRole('textbox', { name: 'Device name' }), 'Desktop');
    await userEvent.click(canvas.getByRole('button', { name: 'Create token' }));
    await expect(await canvas.findByDisplayValue('jdt_example-secret-shown-once')).toBeInTheDocument();
  },
};

/** The server refuses, for example at ten active tokens. */
export const CreateRefused: Story = {
  decorators: [
    withQueryData(seed([])),
    withApiOverride(inboxApi, 'createToken', () =>
      Promise.reject(
        Object.assign(new Error('Request failed'), {
          isAxiosError: true,
          response: { data: { detail: 'at most 10 active tokens; revoke one first' } },
        }),
      ),
    ),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(await canvas.findByRole('textbox', { name: 'Device name' }), 'Eleventh');
    await userEvent.click(canvas.getByRole('button', { name: 'Create token' }));
    await expect(await canvas.findByText(/at most 10 active tokens/)).toBeInTheDocument();
  },
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(inboxApi, 'listTokens', () => neverResolving())],
};

export const LoadFailed: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(inboxApi, 'listTokens', () => Promise.reject(new Error('Network error'))),
  ],
};
