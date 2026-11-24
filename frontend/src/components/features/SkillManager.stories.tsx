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
} satisfies Meta<typeof SkillManager>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Populated: Story = {
  decorators: [withQueryData(seed(skills))],
};

export const Empty: Story = {
  decorators: [withQueryData(seed([]))],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(skillApi, 'getSkills', () => neverResolving())],
};
