import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { characterApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { CharacterProfileManager } from './CharacterProfileManager';
import type { CharacterProfile } from '../../types';

const profile: CharacterProfile = {
  constitution_id: 'user-1-character',
  version: 3,
  name: 'Janus',
  identity: 'A calm, thorough assistant who prefers to double-check before acting on anything irreversible.',
  core_values: ['Be useful', 'Be honest, even when it is inconvenient', 'Protect the user\'s time'],
  non_negotiables: ['Never send external messages without explicit confirmation', 'Never fabricate data'],
  traits: [],
};

const seed = (data: CharacterProfile | undefined) => (queryClient: QueryClient) => {
  if (data) {
    queryClient.setQueryData(['character-profile'], data);
  }
};

const meta = {
  title: 'Features/CharacterProfileManager',
  component: CharacterProfileManager,
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof CharacterProfileManager>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {
  decorators: [withQueryData(seed(profile))],
};

export const MinimalConstitution: Story = {
  decorators: [
    withQueryData(
      seed({
        constitution_id: 'user-1-character',
        version: 1,
        name: 'Janus',
        identity: 'A thoughtful assistant.',
        core_values: ['Be useful'],
        non_negotiables: ['Be honest'],
        traits: [],
      })
    ),
  ],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(characterApi, 'get', () => neverResolving())],
};

export const LoadError: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(characterApi, 'get', () => Promise.reject(new Error('network error'))),
  ],
};
