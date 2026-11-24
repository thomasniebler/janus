import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { withQueryData } from '../../../test/storybookQueryClient';
import type { TriggerProposal } from '../../../types';
import { SuggestedAutomations } from './SuggestedAutomations';
import {
  toolRegistrySample,
  triggerEventCatalogSample,
  triggerProposalHistorySample,
  triggerProposalSample,
} from './triggerFixtures';
import { triggerEventCatalogQueryKey } from './triggerEventCatalog';
import { triggerProposalQueryKey } from './triggerProposalCount';

const seed =
  (open: TriggerProposal[], history: TriggerProposal[] = []) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData([...triggerProposalQueryKey, 'proposed'], open);
    queryClient.setQueryData([...triggerProposalQueryKey, 'all'], [...open, ...history]);
    queryClient.setQueryData(['mcp-registry'], toolRegistrySample);
    queryClient.setQueryData(triggerEventCatalogQueryKey, triggerEventCatalogSample);
  };

/**
 * Settings -> Triggers, "Suggested automations" (JAN-235): Janus proposes, the
 * user approves. Each state below is a genuinely different review situation.
 */
const meta = {
  title: 'Features/Triggers/SuggestedAutomations',
  component: SuggestedAutomations,
} satisfies Meta<typeof SuggestedAutomations>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Three open suggestions plus a decided history. */
export const AwaitingReview: Story = {
  decorators: [withQueryData(seed(triggerProposalSample, triggerProposalHistorySample))],
};

/** A tool that is off for unattended runs needs its own tick; drafted after reading mail, with hostile-looking text rendered as text. */
export const BlockedToolAndUntrustedProvenance: Story = {
  decorators: [withQueryData(seed([triggerProposalSample[1]]))],
};

/** A replacement for an approved automation, with the side-by-side diff. */
export const ReplacementDiff: Story = {
  decorators: [withQueryData(seed([triggerProposalSample[2]]))],
};

/** Nothing awaits review, but the History disclosure keeps the last decisions. */
export const HistoryOnly: Story = {
  decorators: [withQueryData(seed([], triggerProposalHistorySample))],
};

/** No suggestions at all: the section renders nothing. */
export const Empty: Story = {
  decorators: [withQueryData(seed([]))],
};
