import type { Meta, StoryObj } from '@storybook/react';
import { CharacterStateEditor } from './CharacterStateEditor';
import { characterStateView } from './characterStateFixtures';

const noop = () => {};

const meta = {
  title: 'Features/CharacterState/CharacterStateEditor',
  component: CharacterStateEditor,
  args: { view: characterStateView, pending: false, onSave: noop, onReset: noop },
} satisfies Meta<typeof CharacterStateEditor>;

export default meta;

type Story = StoryObj<typeof meta>;

/** The current state with every value allowed. */
export const Default: Story = {};

/** The defaults of a fresh user (revision 0). */
export const Defaults: Story = {
  args: { view: { ...characterStateView, state: characterStateView.defaults, revision: 0, updated_at: null } },
};

/** Narrow bounds: only two tones are offered and the focus is switched off. */
export const NarrowBounds: Story = {
  args: {
    view: {
      ...characterStateView,
      state: { ...characterStateView.defaults },
      envelope: {
        ...characterStateView.envelope,
        tone: { default: 'neutral', allowed: ['neutral', 'formal'] },
        focus_allowed: false,
      },
    },
  },
};

/** A save or reset in flight: both buttons are disabled. */
export const Pending: Story = { args: { pending: true } };
