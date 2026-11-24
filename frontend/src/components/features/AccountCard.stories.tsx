import type { Meta, StoryObj } from '@storybook/react';
import { AccountCard } from './AccountCard';

const meta = {
  title: 'Features/AccountCard',
  component: AccountCard,
  args: {
    account: {
      id: 1,
      name: 'Work IMAP',
      email: 'me@work.example',
      imap_server: 'imap.work.example',
      imap_port: 993,
      username: 'me@work.example',
      is_active: true,
    },
    isTesting: false,
    isDeleting: false,
    onEdit: () => undefined,
    onTest: () => undefined,
    onDelete: () => undefined,
  },
} satisfies Meta<typeof AccountCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const PasswordAccount: Story = {};

export const OAuthAccountHasNoEdit: Story = {
  args: { account: { ...meta.args.account, name: 'Gmail', auth_type: 'google' } },
};

export const TestSucceeded: Story = {
  args: { testResult: { status: 'success', message: 'Successfully connected to me@work.example' } },
};

export const TestFailed: Story = {
  args: { testResult: { status: 'error', message: 'IMAP connection failed: authentication rejected' } },
};

export const Testing: Story = { args: { isTesting: true } };
