import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import type React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { User } from '../types';
import { stubMatchMedia } from './matchMedia';

type Shell = React.ComponentType<{ children: React.ReactNode; currentUser: User }>;

const currentUser: User = { id: 1, username: 'janus-admin', is_admin: true, google_linked: false };

/**
 * Shared JAN-248 placement contract for every app shell: exactly one chat view
 * is mounted (docked on desktop, inline on narrow screens, none on `/chat`).
 * The old shells rendered a CSS-hidden sidebar copy and a mobile copy at once,
 * which meant two sockets and two diverging transcripts.
 *
 * Call it from the shell's own test file, which must mock `BriefingChat` as
 * `() => <div>Mock briefing chat</div>` and the api module via `mockApiModule`.
 * (With `isolate: false`, each shell module must be imported by one test file
 * only, so this is a helper rather than a separate test file.)
 */
export const describeShellChatPlacement = (name: string, ShellComponent: Shell) => {
  const renderShell = (path: string) =>
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={[path]}>
          <ShellComponent currentUser={currentUser}>
            <h1>Page content</h1>
          </ShellComponent>
        </MemoryRouter>
      </QueryClientProvider>
    );

  describe(`${name} chat placement (JAN-248)`, () => {
    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it('renders a single chat view, docked in the sidebar, on desktop widths', () => {
      stubMatchMedia(true);
      renderShell('/');

      expect(screen.getAllByText('Mock briefing chat')).toHaveLength(1);
      const sidebar = screen.getByRole('complementary', { name: 'Chat with Janus' });
      expect(within(sidebar).getByText('Mock briefing chat')).toBeInTheDocument();
    });

    it('renders a single chat view inline below the page on narrow widths', () => {
      stubMatchMedia(false);
      renderShell('/');

      expect(screen.getAllByText('Mock briefing chat')).toHaveLength(1);
      expect(screen.queryByRole('complementary', { name: 'Chat with Janus' })).not.toBeInTheDocument();
      const inline = screen.getByRole('region', { name: 'Chat with Janus' });
      expect(within(inline).getByText('Mock briefing chat')).toBeInTheDocument();
    });

    it.each([true, false])('renders no shell chat view on /chat (desktop=%s)', (isDesktop) => {
      stubMatchMedia(isDesktop);
      renderShell('/chat');

      expect(screen.getByRole('heading', { name: 'Page content' })).toBeInTheDocument();
      expect(screen.queryByText('Mock briefing chat')).not.toBeInTheDocument();
      // The UIv2/UIv3 drawer toggle has nothing to toggle there.
      expect(screen.queryByRole('button', { name: /^chat$/i })).not.toBeInTheDocument();
    });

    it('links to the /chat page from the primary navigation', () => {
      stubMatchMedia(true);
      renderShell('/');

      const chatLinks = screen.getAllByRole('link', { name: /^(\d+\s*)?chat$/i });
      expect(chatLinks.length).toBeGreaterThan(0);
      for (const link of chatLinks) {
        expect(link).toHaveAttribute('href', '/chat');
      }
    });
  });
};
