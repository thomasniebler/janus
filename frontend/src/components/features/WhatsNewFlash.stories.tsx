import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { useEffect } from 'react';
import { statusApi } from '../../api';
import { withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { WhatsNewFlash } from './WhatsNewFlash';

const notes = {
  data: {
    version: '1.13.0',
    changelog_url: '/docs/changelog',
    release_url: 'https://github.com/thomasniebler/janus/releases/tag/v1.13.0',
    releases: [
      {
        version: '1.13.0',
        date: '2026-11-11',
        groups: [
          {
            title: 'Features',
            items: ['**chat:** narrate multi-step turns with a plan first', '**stats:** approval stats for Janus proposals'],
          },
          { title: 'Bug Fixes', items: ['**db:** WAL mode and busy timeout for SQLite'] },
        ],
      },
    ],
  },
};

/** Seeds the "last seen" version so the flash has something to compare with. */
const withLastSeen = (version: string | null) => (Story: React.ComponentType) => {
  if (version) window.localStorage.setItem('janus.lastSeenVersion.1', version);
  else window.localStorage.removeItem('janus.lastSeenVersion.1');
  useEffect(() => () => window.localStorage.removeItem('janus.lastSeenVersion.1'), []);
  return <Story />;
};

const meta = {
  title: 'Features/WhatsNewFlash',
  component: WhatsNewFlash,
  args: { userId: 1 },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
    withQueryData(),
  ],
} satisfies Meta<typeof WhatsNewFlash>;

export default meta;
type Story = StoryObj<typeof meta>;

export const AfterAnUpdate: Story = {
  decorators: [withLastSeen('1.12.0'), withApiOverride(statusApi, 'getVersion', async () => notes as never)],
};

export const FirstVisitShowsNothing: Story = {
  decorators: [
    withLastSeen(null),
    withApiOverride(statusApi, 'getVersion', async () => ({ data: { ...notes.data, releases: [] } }) as never),
  ],
};
