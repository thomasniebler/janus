import { composeStories } from '@storybook/react';
import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderStory } from '../../test/storybookTestUtils';
import * as stories from './AppSidebar.stories';

const { Default, DashboardActive } = composeStories(stories);

describe('AppSidebar', () => {
  it('renders navigation links and the signed-in user from the story', () => {
    renderStory(Default);

    expect(screen.getByText('Signed in as')).toBeInTheDocument();
    expect(screen.getByText('janus-admin')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /dashboard/i })).toHaveAttribute('href', '/');
    expect(screen.getByRole('link', { name: /memory & knowledge/i })).toHaveAttribute('href', '/memory');
  });

  it('marks the story-selected route as active', () => {
    renderStory(DashboardActive);

    expect(screen.getByRole('link', { name: /dashboard/i })).toHaveClass('janus-nav-link', 'active');
  });
});
