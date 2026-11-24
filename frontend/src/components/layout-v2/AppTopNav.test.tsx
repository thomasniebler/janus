import { composeStories } from '@storybook/react';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderStory } from '../../test/storybookTestUtils';
import * as stories from './AppTopNav.stories';

const { Default, DashboardActive, ChatDrawerClosed } = composeStories(stories);

const primaryNav = () => screen.getByRole('navigation', { name: 'Primary' });

describe('AppTopNav', () => {
  it('renders the wordmark, the shared nav items, and the signed-in user', () => {
    renderStory(Default);

    expect(screen.getByRole('link', { name: 'Janus home' })).toHaveAttribute('href', '/');
    expect(screen.getByText('janus-admin')).toBeInTheDocument();

    const nav = within(primaryNav());
    expect(nav.getByRole('link', { name: /dashboard/i })).toHaveAttribute('href', '/');
    expect(nav.getByRole('link', { name: /memory & knowledge/i })).toHaveAttribute('href', '/memory');
    expect(nav.getByRole('link', { name: /documentation/i })).toHaveAttribute('href', '/docs');
  });

  it('marks the story-selected route as the current page', () => {
    renderStory(DashboardActive);

    const nav = within(primaryNav());
    expect(nav.getByRole('link', { name: /dashboard/i })).toHaveAttribute('aria-current', 'page');
    expect(nav.getByRole('link', { name: /settings/i })).not.toHaveAttribute('aria-current');
  });

  it('reports the chat drawer state and toggles it', async () => {
    const onToggleChat = vi.fn();
    renderStory(Default, { onToggleChat });
    const user = userEvent.setup();

    const toggle = screen.getByRole('button', { name: /chat/i });
    expect(toggle).toHaveAttribute('aria-pressed', 'true');

    await user.click(toggle);

    expect(onToggleChat).toHaveBeenCalledTimes(1);
  });

  it('shows the drawer toggle as unpressed when the chat is hidden', () => {
    renderStory(ChatDrawerClosed);

    expect(screen.getByRole('button', { name: /chat/i })).toHaveAttribute('aria-pressed', 'false');
  });

  it('logs out from the top bar', async () => {
    const onLogout = vi.fn();
    renderStory(Default, { onLogout });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: /logout/i }));

    expect(onLogout).toHaveBeenCalledTimes(1);
  });
});
