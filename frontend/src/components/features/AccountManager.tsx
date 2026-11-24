import React, { useState } from 'react';
import axios from 'axios';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Link2, RefreshCw } from 'lucide-react';
import { connectionsApi, emailApi } from '../../api';
import { Card } from '../ui/Card';
import { Button } from '../ui/Button';
import { AccountCard } from './AccountCard';
import { AccountFormModal, type AccountForm } from './AccountFormModal';
import type { ConnectionProvider, ConnectionStatus, EmailAccount } from '../../types';

export const AccountManager: React.FC = () => {
  const queryClient = useQueryClient();
  const [showAccountModal, setShowAccountModal] = useState(false);
  const [editingAccountId, setEditingAccountId] = useState<number | null>(null);
  const [showPassword, setShowPassword] = useState(false);
  const [testResults, setTestResults] = useState<Record<string, {status: string, message: string}>>({});
  const emptyForm: AccountForm = {
    name: '', email: '', imap_server: 'imap.gmail.com', imap_port: 993, username: '', password: ''
  };
  const [form, setForm] = useState<AccountForm>(emptyForm);

  const { data: accounts, isLoading } = useQuery({
    queryKey: ['email-accounts'],
    queryFn: async () => (await emailApi.getAccounts()).data,
  });

  const addMutation = useMutation({
    mutationFn: (data: AccountForm) => emailApi.addAccount(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['email-accounts'] });
      setShowAccountModal(false);
      setEditingAccountId(null);
      setForm(emptyForm);
      setShowPassword(false);
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: AccountForm }) =>
      emailApi.updateAccount(id, {
        name: data.name,
        email: data.email,
        imap_server: data.imap_server,
        imap_port: data.imap_port,
        username: data.username,
        ...(data.password.trim() ? { password: data.password } : {}),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['email-accounts'] });
      setShowAccountModal(false);
      setEditingAccountId(null);
      setForm(emptyForm);
      setShowPassword(false);
    },
  });

  const { data: connections } = useQuery({
    queryKey: ['connections'],
    queryFn: async () => (await connectionsApi.getOverview()).data,
  });
  const [connectError, setConnectError] = useState<string | null>(null);

  // A connected Google / Microsoft account gets its mailbox in one click; otherwise the button
  // starts the provider's sign-in, which returns to Settings -> Connections.
  const providerMutation = useMutation({
    mutationFn: async (provider: ConnectionProvider) => {
      const status = connections?.[provider];
      if (status?.connected && status.mail_ready) {
        await connectionsApi.addMailbox(provider);
        return null;
      }
      return (await connectionsApi.getAuthUrl(provider)).data.url;
    },
    onSuccess: (url) => {
      setConnectError(null);
      if (url) {
        window.location.href = url;
        return;
      }
      queryClient.invalidateQueries({ queryKey: ['email-accounts'] });
    },
    onError: (err) =>
      setConnectError(
        axios.isAxiosError(err)
          ? ((err.response?.data as { detail?: string })?.detail ?? err.message)
          : 'Could not add the mailbox.',
      ),
  });

  const providerButton = (provider: ConnectionProvider, label: string, status?: ConnectionStatus) => {
    const ready = Boolean(status?.connected && status.mail_ready);
    return (
      <Button
        variant="secondary"
        onClick={() => providerMutation.mutate(provider)}
        isLoading={providerMutation.isPending && providerMutation.variables === provider}
        disabled={!status?.configured}
        title={status?.configured ? undefined : `Set up ${label} in settings.toml first`}
        icon={<Link2 size={16} />}
      >
        {ready ? `Add ${label} mailbox` : `Connect ${label}`}
      </Button>
    );
  };

  const deleteMutation = useMutation({
    mutationFn: (id: number) => emailApi.deleteAccount(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['email-accounts'] }),
  });

  const testMutation = useMutation({
    mutationFn: (id: number) => emailApi.testAccount(id),
    onSuccess: (data, id) => setTestResults(prev => ({ ...prev, [id]: data.data })),
    onError: (err, id) => setTestResults(prev => ({
      ...prev,
      [id]: {
        status: 'error',
        message: axios.isAxiosError(err) ? err.message : 'Connection test failed.',
      },
    })),
  });

  const openAddModal = () => {
    setEditingAccountId(null);
    setForm(emptyForm);
    setShowPassword(false);
    setShowAccountModal(true);
  };

  const openEditModal = (account: EmailAccount) => {
    setEditingAccountId(account.id);
    setForm({
      name: account.name,
      email: account.email,
      imap_server: account.imap_server,
      imap_port: account.imap_port,
      username: account.username,
      password: '',
    });
    setShowPassword(false);
    setShowAccountModal(true);
  };

  const closeModal = () => {
    setShowAccountModal(false);
    setEditingAccountId(null);
    setForm(emptyForm);
    setShowPassword(false);
  };

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (editingAccountId === null) {
      addMutation.mutate(form);
      return;
    }

    updateMutation.mutate({ id: editingAccountId, data: form });
  };

  const isSaving = addMutation.isPending || updateMutation.isPending;
  const isEditing = editingAccountId !== null;

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-bold">Connected Accounts</h2>
        <div className="flex flex-wrap gap-2">
          {providerButton('google', 'Google', connections?.google)}
          {connections?.microsoft?.mail_supported && providerButton('microsoft', 'Microsoft', connections.microsoft)}
          <Button onClick={openAddModal} icon={<Plus size={16} />}>Custom IMAP</Button>
        </div>
      </div>
      {connectError && (
        <div role="alert" className="p-3 rounded-xl text-sm bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400">
          {connectError}
        </div>
      )}

      <div className="grid gap-4">
        {isLoading ? (
          <Card className="h-32 flex items-center justify-center">
            <RefreshCw className="animate-spin text-primary" />
          </Card>
        ) : accounts && accounts.length > 0 ? (
          accounts.map((acc: EmailAccount) => (
            <AccountCard
              key={acc.id}
              account={acc}
              testResult={testResults[acc.id]}
              isTesting={testMutation.isPending && testMutation.variables === acc.id}
              isDeleting={deleteMutation.isPending && deleteMutation.variables === acc.id}
              onEdit={() => openEditModal(acc)}
              onTest={() => testMutation.mutate(acc.id)}
              onDelete={() => deleteMutation.mutate(acc.id)}
            />
          ))
        ) : (
          <div className="p-12 text-center bg-white dark:bg-dark-card rounded-2xl border border-dashed border-gray-200 dark:border-gray-700">
            <p className="text-gray-500 dark:text-gray-400 text-sm">No email accounts connected.</p>
          </div>
        )}
      </div>

      <AccountFormModal
        isOpen={showAccountModal}
        isEditing={isEditing}
        isSaving={isSaving}
        form={form}
        onChange={setForm}
        showPassword={showPassword}
        onTogglePassword={() => setShowPassword(!showPassword)}
        onSubmit={handleSubmit}
        onClose={closeModal}
      />
    </div>
  );
};
