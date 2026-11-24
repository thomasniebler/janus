import type { Meta, StoryObj } from '@storybook/react';
import { expect, within } from 'storybook/test';

import type { ImageUIBlock } from '../../../lib/uiBlocks';
import { ImageBlock } from './ImageBlock';

// `/api/images/storybook-sample` is a sample PNG served by Storybook (.storybook/main.ts
// staticDirs); in the app the same path is the authenticated, owner-scoped image endpoint.
const generated: ImageUIBlock = {
  id: 'blk_image0000253',
  version: 1,
  type: 'image',
  alt: 'A lighthouse at dusk, watercolor',
  url: '/api/images/storybook-sample',
  caption: 'Generated with OpenAI gpt-image-1',
};

/** An image Janus generated (JAN-253) or any same-origin `/api/images/<id>` image block (JAN-252). */
const meta = {
  title: 'Features/UIBlocks/ImageBlock',
  component: ImageBlock,
  decorators: [
    (Story) => (
      <div className="max-w-md p-4">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof ImageBlock>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Loaded: the picture, its caption, and a Download link to the same authenticated URL. */
export const LoadedWithDownload: Story = {
  args: { block: generated },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole('img', { name: generated.alt })).toHaveAttribute('src', generated.url);
    const download = canvas.getByRole('link', { name: 'Download' });
    await expect(download).toHaveAttribute('href', generated.url);
    await expect(download).toHaveAttribute('download');
  },
};

/** With a title and no caption: the Download link stands alone under the picture. */
export const TitledWithoutCaption: Story = {
  args: { block: { ...generated, title: 'Lighthouse', caption: undefined } },
};

/** No URL (the model only described an image): the alt text placeholder, no download link. */
export const Unavailable: Story = {
  args: { block: { ...generated, url: undefined } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(`Image unavailable: ${generated.alt}`)).toBeInTheDocument();
    await expect(canvas.queryByRole('link', { name: 'Download' })).toBeNull();
  },
};

/** The image no longer exists (deleted conversation, another user's id): it falls back to the alt text. */
export const DeletedOrForeign: Story = {
  args: { block: { ...generated, url: '/api/images/deleted-image-id' } },
};
