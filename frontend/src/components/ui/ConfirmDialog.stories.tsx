import type { Meta, StoryObj } from '@storybook/react';
import { ConfirmDialog } from './ConfirmDialog';

const meta = {
  title: 'UI/ConfirmDialog',
  component: ConfirmDialog,
  args: {
    isOpen: true,
    title: 'Archive conversation',
    message: 'The conversation moves to the archive. You can restore it later.',
    onConfirm: () => {},
    onCancel: () => {},
  },
} satisfies Meta<typeof ConfirmDialog>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};

/** The unattended-tool warning from the Tools settings page (JAN-275). */
export const Warning: Story = {
  args: {
    title: 'Allow unattended use?',
    tone: 'warning',
    confirmLabel: 'Allow',
    message:
      'Janus will be able to use "Send mail" (Send an email from a connected account) with nobody watching. ' +
      'Every run still records which tools it used.',
  },
};

export const Closed: Story = {
  args: { isOpen: false },
};
