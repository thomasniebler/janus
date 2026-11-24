import type { Meta, StoryObj } from '@storybook/react';
import { Button } from './Button';
import { Modal } from './Modal';

const meta = {
  title: 'UI/Modal',
  component: Modal,
  args: {
    isOpen: true,
    title: 'Delete rule',
    onClose: () => {},
    children: (
      <p className="text-sm text-gray-500 dark:text-gray-400">
        This will permanently remove the rule. This action cannot be undone.
      </p>
    ),
  },
} satisfies Meta<typeof Modal>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Open: Story = {};

export const Closed: Story = {
  args: { isOpen: false },
};

export const WithFormContent: Story = {
  args: {
    title: 'Add email account',
    children: (
      <div className="space-y-4">
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Connect an IMAP account for me@example.com to monitor.
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm">
            Cancel
          </Button>
          <Button size="sm">Connect</Button>
        </div>
      </div>
    ),
  },
};
