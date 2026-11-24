import type { Meta, StoryObj } from '@storybook/react';
import { expect, userEvent, within } from 'storybook/test';
import { knowledgeApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import type { KnowledgeSource } from '../../types';
import { KnowledgeSourcesManager } from './KnowledgeSourcesManager';

const source = (overrides: Partial<KnowledgeSource>): KnowledgeSource => ({
  id: 1,
  source_type: 'upload',
  part: 0,
  title: 'contract.pdf',
  mime_type: 'application/pdf',
  size_bytes: 482_000,
  status: 'ready',
  status_detail: null,
  chunk_count: 12,
  created_at: '2026-09-28T10:00:00Z',
  ...overrides,
});

const populated: KnowledgeSource[] = [
  source({ id: 1 }),
  source({ id: 2, source_type: 'email', title: 'Quarterly report', size_bytes: 3_100, chunk_count: 2 }),
  source({ id: 3, source_type: 'email_attachment', title: 'budget.xlsx', status: 'pending', chunk_count: 0 }),
  source({ id: 4, source_type: 'email_attachment', title: 'photo.jpg', status: 'skipped', status_detail: 'unsupported file type (image/jpeg)', chunk_count: 0 }),
  source({ id: 5, source_type: 'upload', title: 'broken.xlsx', status: 'failed', status_detail: 'FileConversionException: XlsxConverter threw BadZipFile', chunk_count: 0 }),
  source({ id: 6, source_type: 'whatsapp_conversation', title: 'Family', size_bytes: 12_000, chunk_count: 5 }),
];

const listing = (sources: KnowledgeSource[]) =>
  withApiOverride(knowledgeApi, 'listSources', async () => ({ data: sources }) as never);

const meta = {
  title: 'Features/KnowledgeSourcesManager',
  component: KnowledgeSourcesManager,
} satisfies Meta<typeof KnowledgeSourcesManager>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Every status and a mix of source types; only uploads and attachments can be deleted. */
export const Populated: Story = {
  decorators: [withQueryData(), listing(populated)],
};

/** A fresh install with nothing ingested yet. */
export const Empty: Story = {
  decorators: [withQueryData(), listing([])],
};

/** Waiting for the first response. */
export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(knowledgeApi, 'listSources', () => neverResolving())],
};

/** The source list cannot be loaded; the failure is shown instead of an empty list. */
export const LoadError: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(knowledgeApi, 'listSources', async () => {
      throw new Error('server error');
    }),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('alert')).toHaveTextContent('Could not load the sources.');
    await expect(canvas.queryByText(/Nothing ingested yet/)).toBeNull();
  },
};

/** An upload in flight: the button shows its spinner. */
export const Uploading: Story = {
  decorators: [withQueryData(), listing(populated), withApiOverride(knowledgeApi, 'upload', () => neverResolving())],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.upload(canvas.getByLabelText('Document file'), new File(['text'], 'notes.txt', { type: 'text/plain' }));
  },
};

/** The server rejects the upload; the message is shown inline. */
export const UploadRejected: Story = {
  decorators: [
    withQueryData(),
    listing([]),
    withApiOverride(knowledgeApi, 'upload', async () => {
      throw new Error('network down');
    }),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.upload(canvas.getByLabelText('Document file'), new File(['text'], 'notes.txt', { type: 'text/plain' }));
    await expect(await canvas.findByRole('alert')).toHaveTextContent('Upload failed.');
  },
};
