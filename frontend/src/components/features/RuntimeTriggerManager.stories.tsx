import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { runtimeTriggerApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { RuntimeTriggerManager } from './RuntimeTriggerManager';
import { RUNS_PAGE_SIZE } from './triggers/TriggerRunHistory';
import {
  toolRegistrySample,
  triggerDefinitionSample,
  triggerEventCatalogSample,
  triggerProposalSample,
  triggerSample,
} from './triggers/triggerFixtures';
import { triggerEventCatalogQueryKey } from './triggers/triggerEventCatalog';
import { triggerProposalQueryKey } from './triggers/triggerProposalCount';
import type { RuntimeTriggerWorkerStatus, TriggerProposal } from '../../types';

const seed =
  (options: { triggers?: typeof triggerSample; worker?: RuntimeTriggerWorkerStatus; proposals?: TriggerProposal[] }) =>
  (queryClient: QueryClient) => {
    const triggers = options.triggers ?? triggerSample;
    // The unfiltered first page, as `TriggerRunHistory` keys it (undefined filters drop out of the hash).
    queryClient.setQueryData(['runtime-triggers', { limit: RUNS_PAGE_SIZE, offset: 0 }], {
      items: triggers,
      total: triggers.length,
      limit: RUNS_PAGE_SIZE,
      offset: 0,
      has_more: false,
    });
    queryClient.setQueryData(['runtime-trigger-worker'], options.worker ?? { enabled: true, running: true });
    queryClient.setQueryData(['trigger-definitions'], triggerDefinitionSample);
    queryClient.setQueryData(triggerEventCatalogQueryKey, triggerEventCatalogSample);
    queryClient.setQueryData(['mcp-registry'], toolRegistrySample);
    queryClient.setQueryData([...triggerProposalQueryKey, 'proposed'], options.proposals ?? []);
    queryClient.setQueryData([...triggerProposalQueryKey, 'all'], options.proposals ?? []);
  };

const meta = {
  title: 'Features/RuntimeTriggerManager',
  component: RuntimeTriggerManager,
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof RuntimeTriggerManager>;

export default meta;

type Story = StoryObj<typeof meta>;

export const WorkerRunning: Story = {
  decorators: [withQueryData(seed({}))],
};

export const WorkerDisabled: Story = {
  decorators: [withQueryData(seed({ worker: { enabled: false, running: false } }))],
};

export const WorkerEnabledButNotRunning: Story = {
  decorators: [withQueryData(seed({ worker: { enabled: true, running: false } }))],
};

export const NoTriggersYet: Story = {
  decorators: [withQueryData(seed({ triggers: [] }))],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(runtimeTriggerApi, 'list', () => neverResolving())],
};

/** Suggested automations (JAN-235) appear above the definitions, awaiting review. */
export const WithSuggestedAutomations: Story = {
  decorators: [withQueryData(seed({ proposals: triggerProposalSample }))],
};
