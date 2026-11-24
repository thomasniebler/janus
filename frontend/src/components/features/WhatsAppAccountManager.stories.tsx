import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { expect, fn, userEvent, waitFor, within } from 'storybook/test';
import { whatsappApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { WhatsAppAccountManager } from './WhatsAppAccountManager';
import type { WhatsAppAccount } from '../../types';

const connectedAccount: WhatsAppAccount = {
  id: 1,
  display_name: 'Personal WhatsApp',
  provider: 'neonize',
  is_enabled: true,
  connection_status: 'connected',
  initial_history_days: 3,
  last_synced_at: '2026-09-25T09:00:00Z',
  last_sync_error: null,
  session_configured: true,
};

const degradedAccount: WhatsAppAccount = {
  id: 2,
  display_name: 'Work WhatsApp',
  provider: 'neonize',
  is_enabled: true,
  connection_status: 'degraded',
  initial_history_days: 7,
  last_synced_at: '2026-09-20T09:00:00Z',
  last_sync_error: 'Session expired — reconnect required.',
  session_configured: true,
};

const seed =
  (accounts: WhatsAppAccount[]) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['whatsapp-accounts'], accounts);
  };

const meta = {
  title: 'Features/WhatsAppAccountManager',
  component: WhatsAppAccountManager,
} satisfies Meta<typeof WhatsAppAccountManager>;

export default meta;

type Story = StoryObj<typeof meta>;

export const ConnectedAccounts: Story = {
  decorators: [withQueryData(seed([connectedAccount]))],
};

export const DegradedAccountNeedsReconnect: Story = {
  decorators: [withQueryData(seed([connectedAccount, degradedAccount]))],
};

export const NoAccountsYet: Story = {
  decorators: [withQueryData(seed([]))],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(whatsappApi, 'getAccounts', () => neverResolving())],
};

// Deleting an account removes its synced conversations and messages, so the trash
// button asks first (JAN-223). The dialog renders in a portal, so the play
// functions query the document body.
const deleteAccountSpy = fn(async () => ({ data: { status: 'success' } }));
const deleteButtonName = `Delete WhatsApp account "${connectedAccount.display_name}"`;

const openDeleteDialog = async (canvasElement: HTMLElement) => {
  const canvas = within(canvasElement);
  await userEvent.click(await canvas.findByRole('button', { name: deleteButtonName }));
  const dialog = await within(canvasElement.ownerDocument.body).findByRole('dialog', {
    name: 'Delete WhatsApp account?',
  });
  return within(dialog);
};

const deleteDecorators = [
  withQueryData(seed([connectedAccount])),
  withApiOverride(whatsappApi, 'deleteAccount', deleteAccountSpy as unknown as typeof whatsappApi.deleteAccount),
];

export const DeleteConfirmationOpen: Story = {
  decorators: deleteDecorators,
  play: async ({ canvasElement }) => {
    deleteAccountSpy.mockClear();
    const dialog = await openDeleteDialog(canvasElement);
    await expect(dialog.getByText(/deletes its synced conversations and messages/)).toBeInTheDocument();
    await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeEnabled();
    await expect(dialog.getByRole('button', { name: 'Delete' })).toBeEnabled();
    await expect(deleteAccountSpy).not.toHaveBeenCalled();
  },
};

export const DeleteConfirmationCancelled: Story = {
  decorators: deleteDecorators,
  play: async ({ canvasElement }) => {
    deleteAccountSpy.mockClear();
    const dialog = await openDeleteDialog(canvasElement);
    await userEvent.click(dialog.getByRole('button', { name: 'Cancel' }));
    const body = within(canvasElement.ownerDocument.body);
    await waitFor(() => expect(body.queryByRole('dialog')).not.toBeInTheDocument());
    await expect(within(canvasElement).getByText(connectedAccount.display_name)).toBeInTheDocument();
    await expect(deleteAccountSpy).not.toHaveBeenCalled();
  },
};

export const DeleteConfirmed: Story = {
  decorators: deleteDecorators,
  play: async ({ canvasElement }) => {
    deleteAccountSpy.mockClear();
    const dialog = await openDeleteDialog(canvasElement);
    await userEvent.click(dialog.getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(deleteAccountSpy).toHaveBeenCalledWith(connectedAccount.id));
  },
};
