import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { emailApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { AccountManager } from './AccountManager';
import type { EmailAccount } from '../../types';

const accounts: EmailAccount[] = [
  {
    id: 1,
    name: 'Personal Gmail',
    email: 'me@example.com',
    imap_server: 'imap.gmail.com',
    imap_port: 993,
    username: 'me@example.com',
    is_active: true,
  },
  {
    id: 2,
    name: 'Work Outlook',
    email: 'thomas@work.example',
    imap_server: 'outlook.office365.com',
    imap_port: 993,
    username: 'thomas@work.example',
    is_active: true,
  },
];

const seed =
  (data: EmailAccount[]) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['email-accounts'], data);
  };

const meta = {
  title: 'Features/AccountManager',
  component: AccountManager,
  decorators: [withQueryData(seed(accounts))],
} satisfies Meta<typeof AccountManager>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Populated: Story = {};

export const Empty: Story = {
  decorators: [withQueryData(seed([]))],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(emailApi, 'getAccounts', () => neverResolving())],
};
