import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';

import type { User } from '../../types';
import { withChatSession } from '../../test/storybookChatSession';
import { LongTranscriptChatPage } from '../../test/storybookLongTranscript';
import { withFakeWebSocket, withQueryData } from '../../test/storybookQueryClient';
import LayoutV2 from './LayoutV2';

const currentUser: User = {
  id: 1,
  username: 'janus-admin',
  is_admin: true,
  google_linked: false,
  ui_version: 'v2',
};

// The chat drawer opens its /ws/chat socket on mount; withFakeWebSocket keeps that
// socket inside the static Storybook preview, which has no backend to connect to.
const seedEmptyChat = (queryClient: QueryClient) => {
  queryClient.setQueryData(['conversations'], []);
};

const meta = {
  title: 'LayoutV2/LayoutV2',
  component: LayoutV2,
  args: {
    currentUser,
    children: (
      <div className="rounded-none border border-v2-border bg-v2-surface-muted p-6">
        <h1 className="uiv2-meta text-2xl font-bold uppercase tracking-wide">Dashboard</h1>
        <p className="mt-2 text-sm text-v2-text-muted">Page content rendered inside the UIv2 terminal shell.</p>
      </div>
    ),
  },
  decorators: [withChatSession(), withFakeWebSocket(), withQueryData(seedEmptyChat)],
  render: (args) => (
    <MemoryRouter initialEntries={['/']}>
      <LayoutV2 {...args} />
    </MemoryRouter>
  ),
  parameters: { layout: 'fullscreen' },
} satisfies Meta<typeof LayoutV2>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};

/** On the full-width /chat page the shell renders no sidebar or inline chat (JAN-248). */
export const ChatRoute: Story = {
  render: (args) => (
    <MemoryRouter initialEntries={['/chat']}>
      <LayoutV2 {...args} />
    </MemoryRouter>
  ),
};

/**
 * The /chat page with a transcript much taller than the viewport (JAN-273): only
 * the conversation history scrolls, and the footer stays below the page.
 */
export const ChatPageLongTranscript: Story = {
  render: (args) => (
    // The shell's `uiv2:` variants key off `.ui-v2`, which the app puts on <html>.
    <div className="ui-v2">
      <MemoryRouter initialEntries={['/chat']}>
        <LayoutV2 {...args}>
          <LongTranscriptChatPage />
        </LayoutV2>
      </MemoryRouter>
    </div>
  ),
};
