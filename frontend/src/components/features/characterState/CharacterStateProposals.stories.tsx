import type { Meta, StoryObj } from '@storybook/react';
import { CharacterStateProposals } from './CharacterStateProposals';
import { characterStateProposals } from './characterStateFixtures';

const noop = () => {};

const meta = {
  title: 'Features/CharacterState/CharacterStateProposals',
  component: CharacterStateProposals,
  args: { proposals: characterStateProposals, busyId: null, onApprove: noop, onReject: noop },
} satisfies Meta<typeof CharacterStateProposals>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Two open proposals; the second's focus holds markup and is shown as plain text. */
export const Populated: Story = {};

/** Nothing awaits review. */
export const Empty: Story = { args: { proposals: [] } };

/** An approval in flight: that card's buttons are busy and disabled. */
export const Approving: Story = { args: { busyId: characterStateProposals[0].id } };
