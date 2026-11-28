import { composeStories } from '@storybook/react';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderStory } from '../../test/storybookTestUtils';
import * as stories from './SubTabs.stories';

const { FirstSelected, LastSelected } = composeStories(stories);

describe('SubTabs', () => {
  it('marks the active tab and links each tab to its panel', () => {
    renderStory(FirstSelected);

    expect(screen.getByRole('tab', { name: 'Mail' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Messaging' })).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByRole('tab', { name: 'Mail' })).toHaveAttribute('aria-controls', 'story-panel-mail');
  });

  it('reports a clicked tab', async () => {
    const onSelect = vi.fn();
    renderStory(FirstSelected, { onSelect });

    await userEvent.setup().click(screen.getByRole('tab', { name: 'Messaging' }));

    expect(onSelect).toHaveBeenCalledWith('messaging');
  });

  it('moves with the arrow keys and wraps around at both ends', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    renderStory(LastSelected, { onSelect });

    screen.getByRole('tab', { name: 'Developer' }).focus();
    await user.keyboard('{ArrowRight}');
    expect(onSelect).toHaveBeenLastCalledWith('mail');

    await user.keyboard('{ArrowLeft}');
    expect(onSelect).toHaveBeenLastCalledWith('developer');

    await user.keyboard('{Home}');
    expect(onSelect).toHaveBeenLastCalledWith('mail');
    await user.keyboard('{End}');
    expect(onSelect).toHaveBeenLastCalledWith('developer');
  });
});
