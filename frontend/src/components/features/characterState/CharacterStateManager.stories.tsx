import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { characterStateApi } from '../../../lib/characterState';
import { neverResolving, withApiOverride, withQueryData } from '../../../test/storybookQueryClient';
import type { CharacterStateView } from '../../../lib/characterState';
import { CharacterStateManager } from './CharacterStateManager';
import { characterStateHistory, characterStateProposals, characterStateView } from './characterStateFixtures';
import {
  characterStateHistoryQueryKey,
  characterStateProposalsQueryKey,
  characterStateQueryKey,
} from './characterStateShared';

const seed =
  (view: CharacterStateView, history = characterStateHistory, proposals = characterStateProposals) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(characterStateQueryKey, view);
    queryClient.setQueryData(characterStateHistoryQueryKey, { pages: [{ items: history, next_before_id: null }], pageParams: [null] });
    queryClient.setQueryData(characterStateProposalsQueryKey, proposals);
  };

const meta = {
  title: 'Features/CharacterStateManager',
  component: CharacterStateManager,
} satisfies Meta<typeof CharacterStateManager>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Two open proposals (one focus with markup, shown as plain text) and a two-entry history. */
export const WithProposals: Story = {
  decorators: [withQueryData(seed(characterStateView))],
};

/** A fresh user: the defaults, no history, no proposals. */
export const Defaults: Story = {
  decorators: [
    withQueryData(
      seed({ ...characterStateView, state: characterStateView.defaults, revision: 0, updated_at: null }, [], [])
    ),
  ],
};

/** Narrow bounds: two tones, no focus. */
export const NarrowBounds: Story = {
  decorators: [
    withQueryData(
      seed(
        {
          ...characterStateView,
          state: { ...characterStateView.defaults },
          envelope: { ...characterStateView.envelope, tone: { default: 'neutral', allowed: ['neutral', 'formal'] }, focus_allowed: false },
        },
        [],
        []
      )
    ),
  ],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(characterStateApi, 'get', () => neverResolving())],
};

export const LoadError: Story = {
  decorators: [withQueryData(), withApiOverride(characterStateApi, 'get', () => Promise.reject(new Error('network error')))],
};
