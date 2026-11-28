import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { expect, userEvent, within } from 'storybook/test';
import { memoryApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { MemoryManager } from './MemoryManager';
import type { AgentMemoryCompactionRun, AgentMemoryFact } from '../../types';

const facts: AgentMemoryFact[] = [
  {
    id: 1,
    summary: 'I prefer afternoon meetings.',
    detail: 'Mornings are best for focused work; avoid scheduling calls before 11am.',
    source: 'user',
    status: 'active',
    source_conversation_id: 4,
    compacted_into_id: null,
    created_at: '2026-08-01T09:00:00Z',
    updated_at: '2026-08-01T09:00:00Z',
    last_accessed_at: '2026-09-20T10:00:00Z',
  },
  {
    id: 2,
    summary: 'Prefers concise email summaries over full transcripts.',
    detail: null,
    source: 'agent',
    status: 'active',
    source_conversation_id: null,
    compacted_into_id: null,
    created_at: '2026-08-15T09:00:00Z',
    updated_at: '2026-08-15T09:00:00Z',
    last_accessed_at: null,
  },
  {
    id: 3,
    summary: 'Old note about a since-completed project.',
    detail: null,
    source: 'compaction',
    status: 'archived',
    source_conversation_id: null,
    compacted_into_id: 2,
    created_at: '2026-05-01T09:00:00Z',
    updated_at: '2026-08-15T09:05:00Z',
    last_accessed_at: null,
  },
];

/** One fact per memory-review state, with evidence, an automatic index entry, a pin and a hidden fact. */
const reviewedFacts: AgentMemoryFact[] = [
  {
    ...facts[0],
    id: 11,
    summary: 'Daughter Mia swims on Tuesdays at 5pm.',
    detail: null,
    source: 'agent',
    verification: 'confirmed',
    evidence_quote: 'my daughter Mia has swimming every Tuesday at 5pm',
    verified_at: '2026-09-21T03:17:00Z',
    promoted: true,
    pin: null,
    in_memory_index: true,
  },
  {
    ...facts[0],
    id: 12,
    summary: 'Dentist is Dr. Okafor in Leipzig.',
    detail: null,
    source: 'agent',
    verification: 'corrected',
    evidence_quote: 'My dentist is Dr. Okafor in Leipzig.',
    verified_at: '2026-09-21T03:17:00Z',
    pin: 'pinned',
    in_memory_index: true,
  },
  { ...facts[1], id: 13, verification: 'unverified', pin: 'hidden', in_memory_index: false },
  { ...facts[1], id: 14, summary: 'Plans a trip to Porto in May.', verification: 'unsupported', in_memory_index: false },
  {
    ...facts[2],
    id: 15,
    summary: 'Works from the Berlin office on Fridays.',
    source: 'agent',
    verification: 'contradicted',
    evidence_quote: 'I stopped going to the office on Fridays.',
    in_memory_index: false,
  },
];

const runningCompaction: AgentMemoryCompactionRun = {
  id: 9,
  status: 'running',
  result_summary: null,
  error_message: null,
  facts_considered: 12,
  facts_archived: 0,
  facts_created: 0,
  created_at: '2026-09-25T09:00:00Z',
  started_at: '2026-09-25T09:00:02Z',
  completed_at: null,
};

const completedCompaction: AgentMemoryCompactionRun = {
  id: 8,
  status: 'completed',
  result_summary: 'Merged 3 stale project notes into 1 durable fact.',
  error_message: null,
  facts_considered: 12,
  facts_archived: 3,
  facts_created: 1,
  created_at: '2026-09-20T09:00:00Z',
  started_at: '2026-09-20T09:00:02Z',
  completed_at: '2026-09-20T09:00:45Z',
};

const seed =
  (options: { facts?: AgentMemoryFact[]; latestRun?: AgentMemoryCompactionRun | null }) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['memory-facts', '', false], options.facts ?? facts);
    queryClient.setQueryData(['memory-compaction-latest'], options.latestRun ?? null);
  };

const meta = {
  title: 'Features/MemoryManager',
  component: MemoryManager,
} satisfies Meta<typeof MemoryManager>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Populated: Story = {
  decorators: [withQueryData(seed({ latestRun: completedCompaction }))],
};

/** The memory review's states: verification badges, evidence quotes, and the index menu (automatic, pinned, hidden). */
export const ReviewStates: Story = {
  decorators: [withQueryData(seed({ facts: reviewedFacts, latestRun: completedCompaction }))],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('corrected')).toBeInTheDocument();
    await expect(canvas.getAllByText('in index')).toHaveLength(2);
    await expect(
      canvas.getByRole('combobox', { name: 'Memory index for "Dentist is Dr. Okafor in Leipzig."' }),
    ).toHaveValue('pinned');
  },
};

export const Empty: Story = {
  decorators: [withQueryData(seed({ facts: [], latestRun: null }))],
};

export const CompactionRunning: Story = {
  decorators: [withQueryData(seed({ latestRun: runningCompaction }))],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(memoryApi, 'list', () => neverResolving())],
};

/** A fact opened for editing in place (JAN-224): its summary and detail are prefilled. */
export const Editing: Story = {
  decorators: [withQueryData(seed({ latestRun: completedCompaction }))],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByRole('button', { name: 'Edit memory fact "I prefer afternoon meetings."' }));
    const form = canvas.getByRole('form', { name: 'Edit memory fact "I prefer afternoon meetings."' });
    await expect(within(form).getByLabelText('Summary')).toHaveValue('I prefer afternoon meetings.');
  },
};

/** Saving an edit fails; the backend's reason is shown and the editor stays open. */
export const EditFailed: Story = {
  decorators: [
    withQueryData(seed({ latestRun: completedCompaction })),
    withApiOverride(memoryApi, 'update', async () => {
      throw { response: { status: 404, data: { detail: 'Memory fact not found' } } };
    }),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByRole('button', { name: 'Edit memory fact "I prefer afternoon meetings."' }));
    await userEvent.click(canvas.getByRole('button', { name: 'Save changes' }));
    await expect(await canvas.findByRole('alert')).toHaveTextContent('Memory fact not found');
  },
};
