import { composeStories } from '@storybook/react';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderStory } from '../../test/storybookTestUtils';
import * as stories from './PageSectionNav.stories';

const { Default, MonitoringFocused } = composeStories(stories);

describe('PageSectionNav', () => {
  it('renders section headings and exposes the active tab from the story', () => {
    renderStory(MonitoringFocused);

    expect(screen.getByText('Workspace')).toBeInTheDocument();
    expect(screen.getByText('Monitoring')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /system status/i })).toHaveAttribute('aria-selected', 'true');
  });

  it('moves focus and reports the next tab when using keyboard navigation', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    renderStory(Default, { onSelect });

    const accountsTab = screen.getByRole('tab', { name: /email accounts/i });
    accountsTab.focus();

    await user.keyboard('{ArrowRight}');

    expect(onSelect).toHaveBeenCalledWith('security');
    expect(screen.getByRole('tab', { name: /security/i })).toHaveFocus();
  });
});
