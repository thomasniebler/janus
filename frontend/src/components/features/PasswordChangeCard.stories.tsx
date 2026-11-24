import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { withQueryData } from '../../test/storybookQueryClient';
import { PasswordChangeCard } from './PasswordChangeCard';

const meta = {
  title: 'Features/PasswordChangeCard',
  component: PasswordChangeCard,
  decorators: [
    withQueryData(),
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof PasswordChangeCard>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};

/** Filled with matching passwords: the submit button is enabled. */
export const ReadyToSubmit: Story = {
  args: { initialForm: { currentPassword: 'old-secret', newPassword: 'new-secret', confirmPassword: 'new-secret' } },
};

/** The repeated password differs: inline error, submit disabled. */
export const PasswordMismatch: Story = {
  args: { initialForm: { currentPassword: 'old-secret', newPassword: 'new-secret', confirmPassword: 'other-secret' } },
};

/** Over bcrypt's 72-byte limit (37 two-byte characters = 74 bytes): inline error, submit disabled. */
export const PasswordTooLong: Story = {
  args: {
    initialForm: { currentPassword: 'old-secret', newPassword: 'é'.repeat(37), confirmPassword: 'é'.repeat(37) },
  },
};
