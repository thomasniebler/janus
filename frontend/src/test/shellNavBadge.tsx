import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import type React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { triggerProposalCountQueryKey } from '../components/features/triggers/triggerProposalCount';
import { conversationsQueryKey } from '../components/features/janusConversations';
import type { ConversationSummary, User } from '../types';
import { stubMatchMedia } from './matchMedia';

type Shell = React.ComponentType<{ children: React.ReactNode; currentUser: User }>;

const currentUser: User = { id: 1, username: 'janus-admin', is_admin: true, google_linked: false };

/**
 * Shared JAN-235 contract for every app shell: the Settings entry of the
 * primary navigation shows how many suggested automations await review, and
 * nothing when there are none. The count is seeded into the query cache rather
 * than mocked on the api module: the count hook is shared by every shell, so
 * under `isolate: false` it stays bound to whichever file's API mock loaded it
 * first (see `triggerProposalCount.ts`). The calling test file must mock the
 * api module via `mockApiModule`; a failed refetch keeps the seeded count.
 */
export const describeShellNavBadge = (name: string, ShellComponent: Shell) => {
  const renderShell = (open: number, conversations: ConversationSummary[] = []) => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    queryClient.setQueryData(triggerProposalCountQueryKey, { open });
    queryClient.setQueryData(conversationsQueryKey, conversations);
    return render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/memory']}>
          <ShellComponent currentUser={currentUser}>
            <h1>Page content</h1>
          </ShellComponent>
        </MemoryRouter>
      </QueryClientProvider>
    );
  };

  describe(`${name} suggested-automations badge (JAN-235)`, () => {
    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it('shows the open proposal count on the Settings entry', async () => {
      stubMatchMedia(true);
      renderShell(2);

      const badges = await screen.findAllByLabelText('2 suggested automations awaiting review');
      expect(badges.length).toBeGreaterThan(0);
      for (const badge of badges) {
        expect(badge.closest('a')).toHaveAttribute('href', '/settings');
        expect(within(badge).getByText('2')).toBeInTheDocument();
      }
    });

    it('shows no badge when nothing awaits review', async () => {
      stubMatchMedia(true);
      renderShell(0);

      await screen.findByRole('heading', { name: 'Page content' });
      expect(screen.queryByTestId('nav-badge')).not.toBeInTheDocument();
    });

    it('links the Agent Tasks page from the primary navigation (JAN-204)', async () => {
      stubMatchMedia(true);
      renderShell(0);

      const links = await screen.findAllByRole('link', { name: /agent tasks/i });
      expect(links.length).toBeGreaterThan(0);
      for (const link of links) expect(link).toHaveAttribute('href', '/tasks');
    });
  });

  describe(`${name} new Janus conversations badge (JAN-319)`, () => {
    afterEach(() => {
      vi.unstubAllGlobals();
    });

    const conversation = (id: number, origin: 'user' | 'janus', unread: boolean): ConversationSummary => ({
      id,
      title: `Chat ${id}`,
      created_at: '2026-10-11T06:00:00Z',
      updated_at: '2026-10-11T06:00:00Z',
      origin,
      unread,
    });

    it('shows the unread Janus-started count on the Chat entry', async () => {
      stubMatchMedia(true);
      renderShell(0, [conversation(3, 'janus', true), conversation(2, 'janus', false), conversation(1, 'user', false)]);

      const badges = await screen.findAllByLabelText('1 new conversation from Janus');
      expect(badges.length).toBeGreaterThan(0);
      for (const badge of badges) {
        expect(badge.closest('a')).toHaveAttribute('href', '/chat');
      }
    });

    it('shows no Chat badge once every Janus-started conversation was opened', async () => {
      stubMatchMedia(true);
      renderShell(0, [conversation(2, 'janus', false), conversation(1, 'user', false)]);

      await screen.findByRole('heading', { name: 'Page content' });
      expect(screen.queryByTestId('nav-badge')).not.toBeInTheDocument();
    });
  });
};
