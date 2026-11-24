import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, within } from 'storybook/test';
import { JanusMarkdown } from './JanusMarkdown';

const meta = {
  title: 'Features/JanusMarkdown',
  component: JanusMarkdown,
  decorators: [
    (Story) => (
      <MemoryRouter>
        <div className="prose prose-sm dark:prose-invert max-w-none">
          <Story />
        </div>
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof JanusMarkdown>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Ordinary Markdown with an external link. */
export const Plain: Story = {
  args: { children: '## Today\n\n- Two meetings, nothing overdue.\n- See the [release notes](https://example.com/notes).' },
};

/** `[[Page Title]]` and `[[Page Title|label]]` become links to the Wiki tab (JAN-331). */
export const WikiLinks: Story = {
  args: {
    children: 'The [[Acme renewal]] is due on 1 November; [[Dana Whitfield|Dana]] owns the draft.',
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole('link', { name: 'Dana' })).toHaveAttribute(
      'href',
      '/memory?tab=wiki&wiki=Dana%20Whitfield',
    );
  },
};

/** Brackets in code, numeric markers, and fenced code stay as written. */
export const LeftAsWritten: Story = {
  args: {
    children: 'Write `[[Page Title]]` to link a page. Footnote style [[3]] is not a link.\n\n```\n[[Not linked]]\n```',
  },
};
