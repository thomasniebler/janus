import type { Meta, StoryObj } from '@storybook/react';
import { CharacterStateHistory } from './CharacterStateHistory';
import { characterStateHistory } from './characterStateFixtures';

const noop = () => {};

const meta = {
  title: 'Features/CharacterState/CharacterStateHistory',
  component: CharacterStateHistory,
  args: { changes: characterStateHistory, currentRevision: 2, busyId: null, onRestore: noop, onLoadMore: noop },
} satisfies Meta<typeof CharacterStateHistory>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Two changes; only the earlier one offers Restore. */
export const Populated: Story = {};

/** No changes yet: the defaults apply. */
export const Empty: Story = { args: { changes: [], currentRevision: 0 } };

/** Older changes exist beyond the loaded page. */
export const MoreToLoad: Story = { args: { hasMore: true } };

/** The next page is loading. */
export const LoadingMore: Story = { args: { hasMore: true, loadingMore: true } };

/** A restore in flight. */
export const Restoring: Story = { args: { busyId: characterStateHistory[1].id } };

/** The history could not be loaded. */
export const LoadError: Story = { args: { changes: [], currentRevision: 0, loadError: true } };
