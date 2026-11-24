import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { stubMatchMedia } from '../../test/matchMedia';
import { describeShellChatPlacement } from '../../test/shellChatPlacement';
import { describeShellNavBadge } from '../../test/shellNavBadge';

import type { User } from '../../types';
import LayoutV2 from './LayoutV2';

vi.mock('../features/BriefingChat', () => ({
  BriefingChat: () => <div>Mock briefing chat</div>,
}));

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const currentUser: User = {
  id: 1,
  username: 'janus-admin',
  is_admin: true,
  google_linked: false,
  ui_version: 'v2',
};

const renderLayout = (initialPath = '/memory') =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[initialPath]}>
        <LayoutV2 currentUser={currentUser}>
          <h1>Page content</h1>
        </LayoutV2>
      </MemoryRouter>
    </QueryClientProvider>
  );

describe('LayoutV2', () => {
  // Desktop width: the chat lives in the right-hand drawer (see shellChatPlacement.test.tsx
  // for the narrow-viewport and /chat placements).
  beforeEach(() => {
    stubMatchMedia(true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the shared page content inside the UIv2 top-nav shell', () => {
    renderLayout();

    expect(screen.getByRole('heading', { name: 'Page content' })).toBeInTheDocument();
    const nav = within(screen.getByRole('navigation', { name: 'Primary' }));
    expect(nav.getByRole('link', { name: /dashboard/i })).toHaveAttribute('href', '/');
    expect(nav.getByRole('link', { name: /memory & knowledge/i })).toHaveAttribute('aria-current', 'page');
    expect(nav.getByRole('link', { name: /agent tasks/i })).toHaveAttribute('href', '/tasks');
  });

  it('opens with the chat drawer visible and hides it on toggle', async () => {
    renderLayout();
    const user = userEvent.setup();

    expect(screen.getAllByRole('complementary', { name: 'Chat with Janus' })).toHaveLength(1);

    await user.click(screen.getByRole('button', { name: /chat/i }));

    expect(screen.queryByRole('complementary', { name: 'Chat with Janus' })).not.toBeInTheDocument();
    // Only one chat view is ever mounted (JAN-248): a closed drawer on desktop means none.
    expect(screen.queryByText('Mock briefing chat')).not.toBeInTheDocument();
  });

  // JAN-212: the compact chat's root is `flex-1 min-h-0 flex-col`, so it only gets a
  // bounded height (and its message list only scrolls) when its drawer wrapper is itself
  // a flex column. jsdom has no layout engine, so assert the flex contract on the wrapper.
  it('bounds the compact chat inside a flex-column drawer body so long replies scroll', () => {
    renderLayout();

    const drawer = screen.getByRole('complementary', { name: 'Chat with Janus' });
    const chatBody = within(drawer).getByText('Mock briefing chat').parentElement;

    expect(chatBody).toHaveClass('flex', 'flex-col', 'flex-1', 'min-h-0', 'overflow-hidden');
  });

  it('does not render the classic shell chrome', () => {
    renderLayout();

    expect(document.querySelector('.janus-nav-link')).toBeNull();
    expect(screen.queryByText('Signed in as')).not.toBeInTheDocument();
  });

  it('logs the user out and returns to the login page', async () => {
    const { authApi } = await import('../../api');
    renderLayout();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: /logout/i }));

    expect(authApi.logout).toHaveBeenCalledTimes(1);
  });
});

describeShellChatPlacement('LayoutV2', LayoutV2);

describeShellNavBadge('LayoutV2', LayoutV2);
