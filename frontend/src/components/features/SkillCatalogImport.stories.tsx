import type { Meta, StoryObj } from '@storybook/react';
import { expect, userEvent, within } from 'storybook/test';
import { skillApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import type { RemoteSkillSummary } from '../../types';
import { SkillCatalogImport } from './SkillCatalogImport';

const results: RemoteSkillSummary[] = [
  {
    remote_id: 'incident-response',
    name: 'incident-response',
    description: 'Triage production incidents and propose next steps.',
    license: 'MIT',
    content: '# Incident response',
  },
];

const meta = {
  title: 'Features/SkillCatalogImport',
  component: SkillCatalogImport,
} satisfies Meta<typeof SkillCatalogImport>;

export default meta;

type Story = StoryObj<typeof meta>;

const search = async (canvasElement: HTMLElement, term: string) => {
  const canvas = within(canvasElement);
  await userEvent.type(canvas.getByPlaceholderText(/Search remote skills/), term);
  await userEvent.click(canvas.getByRole('button', { name: 'Search' }));
  return canvas;
};

/** Before a search: only the search form. */
export const Idle: Story = {
  decorators: [withQueryData()],
};

export const WithResults: Story = {
  decorators: [withQueryData(), withApiOverride(skillApi, 'discoverSkills', async () => ({ data: results }) as never)],
  play: async ({ canvasElement }) => {
    const canvas = await search(canvasElement, 'incident');
    await expect(await canvas.findByRole('button', { name: 'Import' })).toBeInTheDocument();
  },
};

export const NoResults: Story = {
  decorators: [withQueryData(), withApiOverride(skillApi, 'discoverSkills', async () => ({ data: [] }) as never)],
  play: async ({ canvasElement }) => {
    const canvas = await search(canvasElement, 'nothing');
    await expect(await canvas.findByText(/No remote skills found/)).toBeInTheDocument();
  },
};

export const Searching: Story = {
  decorators: [withQueryData(), withApiOverride(skillApi, 'discoverSkills', () => neverResolving())],
  play: async ({ canvasElement }) => {
    await search(canvasElement, 'incident');
  },
};
