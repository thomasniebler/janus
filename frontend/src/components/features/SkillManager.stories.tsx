import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { skillApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { SkillManager } from './SkillManager';
import type { SkillSummary } from '../../types';

const skills: SkillSummary[] = [
  {
    id: 1,
    name: 'incident-response',
    description: 'Triage production incidents and propose next steps.',
    license: 'MIT',
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
  },
  {
    id: 2,
    name: 'weekly-digest',
    description: 'Summarize the week\'s email and calendar activity.',
    license: null,
    created_at: '2026-07-15T00:00:00Z',
    updated_at: '2026-07-15T00:00:00Z',
  },
];

const seed =
  (data: SkillSummary[]) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['skills'], data);
  };

const meta = {
  title: 'Features/SkillManager',
  component: SkillManager,
  args: { isAdmin: true },
} satisfies Meta<typeof SkillManager>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Populated: Story = {
  decorators: [withQueryData(seed(skills))],
};

/** A non-admin reads the shared library but gets no create, import, edit or delete controls. */
export const ReadOnly: Story = {
  args: { isAdmin: false },
  decorators: [withQueryData(seed(skills))],
};

export const Empty: Story = {
  decorators: [withQueryData(seed([]))],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(skillApi, 'getSkills', () => neverResolving())],
};
