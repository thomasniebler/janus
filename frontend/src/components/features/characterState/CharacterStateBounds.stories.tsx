import type { Meta, StoryObj } from '@storybook/react';
import { CharacterStateBounds } from './CharacterStateBounds';
import { characterStateView } from './characterStateFixtures';

const noop = () => {};

const meta = {
  title: 'Features/CharacterState/CharacterStateBounds',
  component: CharacterStateBounds,
  args: { envelope: characterStateView.envelope, pending: false, onSave: noop },
} satisfies Meta<typeof CharacterStateBounds>;

export default meta;

type Story = StoryObj<typeof meta>;

/** The built-in envelope: every value allowed. */
export const BuiltIn: Story = {};

/** Narrow bounds: two tones, one verbosity level, no focus. */
export const Narrow: Story = {
  args: {
    envelope: {
      tone: { default: 'formal', allowed: ['neutral', 'formal'] },
      verbosity: { default: 'brief', allowed: ['brief'] },
      proactivity: { default: 'reactive', allowed: ['reactive', 'balanced'] },
      focus_allowed: false,
    },
  },
};

/** A save in flight: the button is disabled. */
export const Pending: Story = { args: { pending: true } };
