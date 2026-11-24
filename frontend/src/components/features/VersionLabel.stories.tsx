import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { statusApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { VersionLabel } from './VersionLabel';

const meta = {
  title: 'Features/VersionLabel',
  component: VersionLabel,
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
    withQueryData(),
  ],
} satisfies Meta<typeof VersionLabel>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Known: Story = {
  decorators: [
    withApiOverride(
      statusApi,
      'getVersion',
      async () =>
        ({
          data: {
            version: '1.13.0',
            changelog_url: '/docs/changelog',
            release_url: 'x',
            releases: [],
          },
        }) as never,
    ),
  ],
};

export const Loading: Story = {
  decorators: [withApiOverride(statusApi, 'getVersion', () => neverResolving())],
};
