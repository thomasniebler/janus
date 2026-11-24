import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';

import type { User } from '../../types';
import { withChatSession } from '../../test/storybookChatSession';
import { LongTranscriptChatPage } from '../../test/storybookLongTranscript';
import { withFakeWebSocket, withQueryData } from '../../test/storybookQueryClient';
import Layout from './Layout';

const currentUser: User = {
  id: 1,
  username: 'janus-admin',
  is_admin: true,
  google_linked: false,
};

// The chat panel opens its /ws/chat socket on mount; withFakeWebSocket keeps that
// socket inside the static Storybook preview, which has no backend to connect to.
const seedEmptyChat = (queryClient: QueryClient) => {
  queryClient.setQueryData(['health-version'], { status: 'ok', version: '2.4.0' });
  queryClient.setQueryData(['conversations'], []);
};

const meta = {
  title: 'Layout/Layout',
  component: Layout,
  args: {
    currentUser,
    children: (
      <div className="rounded-2xl border border-white/10 bg-white/5 p-6">
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <p className="mt-2 text-sm opacity-70">Page content rendered inside the classic UIv1 shell.</p>
      </div>
    ),
  },
  decorators: [withChatSession(), withFakeWebSocket(), withQueryData(seedEmptyChat)],
  render: (args) => (
    <MemoryRouter initialEntries={['/']}>
      <Layout {...args} />
    </MemoryRouter>
  ),
  parameters: { layout: 'fullscreen' },
} satisfies Meta<typeof Layout>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const NonAdminUser: Story = {
  args: {
    currentUser: { ...currentUser, is_admin: false },
  },
};

/** On the full-width /chat page the shell renders no sidebar or inline chat (JAN-248). */
export const ChatRoute: Story = {
  render: (args) => (
    <MemoryRouter initialEntries={['/chat']}>
      <Layout {...args} />
    </MemoryRouter>
  ),
};

/**
 * The /chat page with a transcript much taller than the viewport (JAN-273): only
 * the conversation history scrolls, and the footer stays below the page.
 */
export const ChatPageLongTranscript: Story = {
  render: (args) => (
    <MemoryRouter initialEntries={['/chat']}>
      <Layout {...args}>
        <LongTranscriptChatPage />
      </Layout>
    </MemoryRouter>
  ),
};
