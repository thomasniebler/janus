import { formatServerDateTime } from '../../lib/serverTime';
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, MessageSquare, Pencil, Plus, RefreshCw, ScanSearch, Trash2, TriangleAlert, WifiOff } from 'lucide-react';
import { whatsappApi } from '../../api';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';
import { Input } from '../ui/Input';
import { Modal } from '../ui/Modal';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import type {
  WhatsAppAccount,
  WhatsAppSetupSession,
  WhatsAppSetupSessionCreate,
  WhatsAppSetupSessionFallbackImport,
} from '../../types';
import { getErrorMessage, getSetupSessionRefetchInterval, isPairingInProgress } from './whatsappSetupSession';

type EditForm = {
  display_name: string;
};

type FallbackForm = {
  session_data: string;
  session_database_path: string;
};

const emptyDraft: Required<Pick<WhatsAppSetupSessionCreate, 'display_name'>> = {
  display_name: '',
};

const emptyFallback: FallbackForm = {
  session_data: '',
  session_database_path: '',
};

type ActionResult = { status: string; message: string };

/** The success or error line under the wizard or an account card. */
const ResultBanner: React.FC<{ result: ActionResult }> = ({ result }) => (
  <div
    className={`mt-4 flex items-center gap-2 rounded-xl p-3 text-xs ${
      result.status === 'success'
        ? 'bg-green-50 text-green-600 dark:bg-green-900/20 dark:text-green-400'
        : 'bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400'
    }`}
  >
    {result.status === 'success' ? <Check size={14} /> : <TriangleAlert size={14} />}
    {result.message}
  </div>
);

export const WhatsAppAccountManager: React.FC = () => {
  const queryClient = useQueryClient();
  const { confirm, dialog } = useConfirmDialog();
  const [editingAccountId, setEditingAccountId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<EditForm>(emptyDraft);
  const [draft, setDraft] = useState(emptyDraft);
  const [fallbackForm, setFallbackForm] = useState(emptyFallback);
  const [results, setResults] = useState<Record<string, ActionResult>>({});
  const [setupSessionId, setSetupSessionId] = useState<number | null>(null);
  const [showAdvancedFallback, setShowAdvancedFallback] = useState(false);
  const [reconnectAccount, setReconnectAccount] = useState<WhatsAppAccount | null>(null);

  const accountsQuery = useQuery({
    queryKey: ['whatsapp-accounts'],
    queryFn: async () => (await whatsappApi.getAccounts()).data,
  });

  const setupSessionQuery = useQuery({
    queryKey: ['whatsapp-setup-session', setupSessionId],
    queryFn: async () => (await whatsappApi.getSetupSession(setupSessionId!)).data,
    enabled: setupSessionId !== null,
    refetchInterval: (query) => getSetupSessionRefetchInterval(query.state.data as WhatsAppSetupSession | undefined),
  });

  const setupSession = setupSessionQuery.data;
  const effectiveSetupSession = setupSession;

  const resetWizard = () => {
    setSetupSessionId(null);
    setReconnectAccount(null);
    setShowAdvancedFallback(false);
    setFallbackForm(emptyFallback);
    setDraft(emptyDraft);
  };

  const openEditModal = (account: WhatsAppAccount) => {
    setEditingAccountId(account.id);
    setEditForm({ display_name: account.display_name });
  };

  const createSetupSessionMutation = useMutation({
    mutationFn: (payload: WhatsAppSetupSessionCreate) => whatsappApi.createSetupSession(payload),
    onSuccess: (response) => {
      setSetupSessionId(response.data.id);
      queryClient.setQueryData(['whatsapp-setup-session', response.data.id], response.data);
      setShowAdvancedFallback(false);
      setFallbackForm(emptyFallback);
      queryClient.invalidateQueries({ queryKey: ['whatsapp-setup-session', response.data.id] });
    },
    onError: (error) =>
      setResults((current) => ({
        ...current,
        wizard: { status: 'error', message: getErrorMessage(error, 'Failed to start WhatsApp setup.') },
      })),
  });

  const startPairingMutation = useMutation({
    mutationFn: (id: number) => whatsappApi.startPairing(id),
    onSuccess: (response) => {
      queryClient.setQueryData(['whatsapp-setup-session', response.data.id], response.data);
      queryClient.invalidateQueries({ queryKey: ['whatsapp-setup-session', response.data.id] });
    },
    onError: (error) =>
      setResults((current) => ({
        ...current,
        wizard: { status: 'error', message: getErrorMessage(error, 'Failed to start pairing.') },
      })),
  });

  const cancelSetupMutation = useMutation({
    mutationFn: (id: number) => whatsappApi.cancelSetupSession(id),
    onSuccess: (response) => {
      queryClient.setQueryData(['whatsapp-setup-session', response.data.id], response.data);
    },
  });

  const fallbackImportMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: WhatsAppSetupSessionFallbackImport }) =>
      whatsappApi.fallbackImport(id, payload),
    onSuccess: (response) => {
      queryClient.setQueryData(['whatsapp-setup-session', response.data.id], response.data);
      setResults((current) => ({
        ...current,
        wizard: { status: 'success', message: 'Fallback session imported. Finish setup to connect the account.' },
      }));
    },
    onError: (error) =>
      setResults((current) => ({
        ...current,
        wizard: { status: 'error', message: getErrorMessage(error, 'Fallback import failed.') },
      })),
  });

  const completeSetupMutation = useMutation({
    mutationFn: (id: number) => whatsappApi.completeSetupSession(id),
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ['whatsapp-accounts'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
      setResults((current) => ({
        ...current,
        wizard: {
          status: 'success',
          message: `Connected ${response.data.account.display_name}. ${response.data.sync_result.message}`,
        },
      }));
      resetWizard();
    },
    onError: (error) =>
      setResults((current) => ({
        ...current,
        wizard: { status: 'error', message: getErrorMessage(error, 'Failed to finish WhatsApp setup.') },
      })),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: EditForm }) => whatsappApi.updateAccount(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['whatsapp-accounts'] });
      setEditingAccountId(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => whatsappApi.deleteAccount(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['whatsapp-accounts'] }),
  });

  const testMutation = useMutation({
    mutationFn: (id: number) => whatsappApi.testAccount(id),
    onSuccess: (response, id) => setResults((current) => ({ ...current, [id]: response.data })),
    onError: (error, id) =>
      setResults((current) => ({
        ...current,
        [id]: { status: 'error', message: getErrorMessage(error, 'Connection test failed.') },
      })),
  });

  const restartConnectorMutation = useMutation({
    mutationFn: (id: number) => whatsappApi.restartConnector(id),
    onSuccess: (response, id) => {
      queryClient.invalidateQueries({ queryKey: ['whatsapp-accounts'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
      setResults((current) => ({
        ...current,
        [id]: { status: 'success', message: response.data.message },
      }));
    },
    onError: (error, id) =>
      setResults((current) => ({
        ...current,
        [id]: { status: 'error', message: getErrorMessage(error, 'Failed to restart the WhatsApp connector.') },
      })),
  });

  const beginSetup = (account?: WhatsAppAccount | null) => {
    const payload: WhatsAppSetupSessionCreate = {
      display_name: draft.display_name.trim() || account?.display_name || 'WhatsApp',
      account_id: account?.id ?? reconnectAccount?.id ?? null,
    };
    createSetupSessionMutation.mutate(payload);
  };

  const currentAccountContext =
    reconnectAccount ??
    accountsQuery.data?.find((account) => account.id === effectiveSetupSession?.account_id) ??
    null;
  const wizardResult = results.wizard;
  const showFallbackCard = Boolean(
    showAdvancedFallback ||
      effectiveSetupSession?.show_fallback_import ||
      effectiveSetupSession?.setup_status === 'failed' ||
      effectiveSetupSession?.setup_status === 'awaiting_fallback'
  );

  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-emerald-500/10 text-emerald-600">
                <MessageSquare size={24} />
              </div>
              <div>
                <h2 className="text-xl font-bold">WhatsApp Setup</h2>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  Pair Neonize inside Janus when the runtime supports it. If this host cannot pair live, import an
                  existing session as a controlled fallback.
                </p>
              </div>
            </div>
            <div className="flex flex-wrap gap-2 text-xs text-gray-500 dark:text-gray-400">
              <span className="rounded-full bg-gray-100 px-3 py-1 dark:bg-gray-800">1. Intro</span>
              <span className="rounded-full bg-gray-100 px-3 py-1 dark:bg-gray-800">2. Pair in Janus</span>
              <span className="rounded-full bg-gray-100 px-3 py-1 dark:bg-gray-800">3. Fallback import</span>
              <span className="rounded-full bg-gray-100 px-3 py-1 dark:bg-gray-800">4. Finish</span>
            </div>
          </div>
          <Button
            variant="secondary"
            onClick={() => {
              resetWizard();
              setDraft(emptyDraft);
            }}
            icon={<Plus size={16} />}
          >
            New Setup
          </Button>
        </div>

        {wizardResult ? <ResultBanner result={wizardResult} /> : null}

        {setupSessionId === null ? (
          <div className="mt-6 grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
            <div className="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
              <h3 className="font-bold">{currentAccountContext ? `Reconnect ${currentAccountContext.display_name}` : 'Intro + prerequisites'}</h3>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                Janus stores WhatsApp session material locally and encrypted at rest. After pairing, Janus imports the
                message history WhatsApp sends once; how much that is depends on WhatsApp.
              </p>
              <div className="mt-4 grid gap-4 md:grid-cols-2">
                <Input
                  label="Display Name"
                  value={draft.display_name}
                  onChange={(event) => setDraft((current) => ({ ...current, display_name: event.target.value }))}
                  placeholder={currentAccountContext?.display_name ?? 'Personal WhatsApp'}
                />
              </div>
              <div className="mt-4 flex flex-wrap gap-3">
                <Button
                  onClick={() => beginSetup(currentAccountContext)}
                  isLoading={createSetupSessionMutation.isPending}
                >
                  {currentAccountContext ? 'Start reconnect' : 'Start setup'}
                </Button>
                {currentAccountContext ? (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setReconnectAccount(null);
                      setDraft(emptyDraft);
                    }}
                  >
                    Clear reconnect
                  </Button>
                ) : null}
              </div>
            </div>

            <div className="rounded-2xl border border-dashed border-gray-200 p-4 text-sm text-gray-500 dark:border-gray-800 dark:text-gray-400">
              <p className="font-bold text-gray-700 dark:text-gray-300">What Janus stores</p>
              <p className="mt-2">Encrypted session material, account health, bounded synced history, and message metadata needed for later dashboard surfaces.</p>
            </div>
          </div>
        ) : (
          <div className="mt-6 space-y-4">
            <div className="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div>
                  <h3 className="font-bold">Pair in Janus</h3>
                  <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                    {effectiveSetupSession?.pairing_message ?? 'Start pairing to connect this WhatsApp account inside Janus.'}
                  </p>
                  {effectiveSetupSession?.pairing_code ? (
                    <p className="mt-2 rounded-xl bg-gray-100 px-3 py-2 font-mono text-sm dark:bg-gray-800">
                      Pairing code: {effectiveSetupSession.pairing_code}
                    </p>
                  ) : null}
                  {effectiveSetupSession?.pairing_qr_payload?.startsWith('data:image/') ? (
                    <div className="mt-2 inline-block rounded-xl bg-white p-3 dark:bg-gray-100">
                      <img
                        src={effectiveSetupSession.pairing_qr_payload}
                        alt="Scan with WhatsApp on your phone"
                        className="h-48 w-48"
                      />
                    </div>
                  ) : effectiveSetupSession?.pairing_qr_payload ? (
                    <p className="mt-2 rounded-xl bg-gray-100 px-3 py-2 font-mono text-xs break-all dark:bg-gray-800">
                      QR payload: {effectiveSetupSession.pairing_qr_payload}
                    </p>
                  ) : null}
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    onClick={() => startPairingMutation.mutate(setupSessionId)}
                    isLoading={startPairingMutation.isPending}
                    disabled={effectiveSetupSession?.setup_status === 'ready' || isPairingInProgress(effectiveSetupSession)}
                  >
                    Start pairing
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() => cancelSetupMutation.mutate(setupSessionId)}
                    disabled={cancelSetupMutation.isPending || effectiveSetupSession?.setup_status === 'cancelled'}
                  >
                    Cancel
                  </Button>
                  <Button variant="ghost" onClick={() => setShowAdvancedFallback((current) => !current)}>
                    {showAdvancedFallback ? 'Hide fallback' : 'Open fallback'}
                  </Button>
                </div>
              </div>
            </div>

            {showFallbackCard ? (
              <div className="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
                <div className="flex items-start gap-3">
                  <WifiOff className="mt-1 text-amber-500" size={18} />
                  <div className="w-full">
                    <h3 className="font-bold">Fallback import</h3>
                    <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                      Use an existing Neonize session only when live pairing is unavailable on this host or when you
                      need an advanced recovery path.
                    </p>
                    <div className="mt-4 space-y-4">
                      <Input
                        label="Neonize Session DB Path"
                        value={fallbackForm.session_database_path}
                        onChange={(event) =>
                          setFallbackForm((current) => ({ ...current, session_database_path: event.target.value }))
                        }
                        placeholder="/path/to/neonize.db"
                      />
                      <div>
                        <label className="mb-1 block text-xs font-bold uppercase tracking-wider text-gray-400 dark:text-gray-500">
                          Session Data
                        </label>
                        <textarea
                          value={fallbackForm.session_data}
                          onChange={(event) => setFallbackForm((current) => ({ ...current, session_data: event.target.value }))}
                          rows={5}
                          className="w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm dark:border-gray-700 dark:bg-dark-bg"
                          placeholder="Optional serialized Neonize session payload"
                        />
                      </div>
                      <Button
                        onClick={() =>
                          fallbackImportMutation.mutate({
                            id: setupSessionId,
                            payload: {
                              session_data: fallbackForm.session_data.trim() || null,
                              session_database_path: fallbackForm.session_database_path.trim() || null,
                            },
                          })
                        }
                        isLoading={fallbackImportMutation.isPending}
                      >
                        Import fallback session
                      </Button>
                    </div>
                  </div>
                </div>
              </div>
            ) : null}

            <div className="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
              <h3 className="font-bold">Finish</h3>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                Completing setup creates or updates the durable WhatsApp account, saves the session material, and
                starts the live connector. Messages sync automatically as they arrive.
              </p>
              <div className="mt-4 flex flex-wrap gap-3">
                <Button
                  onClick={() => completeSetupMutation.mutate(setupSessionId)}
                  isLoading={completeSetupMutation.isPending}
                  disabled={effectiveSetupSession?.setup_status !== 'ready'}
                >
                  Finish setup
                </Button>
                <Button variant="ghost" onClick={resetWizard}>
                  Start over
                </Button>
              </div>
            </div>
          </div>
        )}
      </Card>

      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold">Configured Accounts</h2>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Manage connected accounts, re-run bounded syncs, or reopen the wizard to reconnect a degraded session.
          </p>
        </div>
      </div>

      <div className="grid gap-4">
        {accountsQuery.isLoading ? (
          <Card className="flex h-32 items-center justify-center">
            <RefreshCw className="animate-spin text-primary" />
          </Card>
        ) : accountsQuery.data && accountsQuery.data.length > 0 ? (
          accountsQuery.data.map((account) => {
            const result = results[account.id];
            return (
              <Card key={account.id} className="p-4">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                  <div className="flex items-start gap-4">
                    <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-emerald-500/10 text-emerald-600">
                      <MessageSquare size={24} />
                    </div>
                    <div>
                      <h3 className="font-bold">{account.display_name}</h3>
                      <p className="text-xs text-gray-500 dark:text-gray-400">
                        {account.provider} · {account.connection_status}
                      </p>
                      {account.last_synced_at ? (
                        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Last synced {formatServerDateTime(account.last_synced_at)}</p>
                      ) : null}
                      {account.last_sync_error ? (
                        <p className="mt-1 text-xs text-red-500 dark:text-red-400">{account.last_sync_error}</p>
                      ) : null}
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setReconnectAccount(account);
                        setDraft({ display_name: account.display_name });
                        setSetupSessionId(null);
                      }}
                    >
                      Reconnect
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => openEditModal(account)} icon={<Pencil size={16} />}>
                      Edit
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => testMutation.mutate(account.id)}
                      isLoading={testMutation.isPending && testMutation.variables === account.id}
                    >
                      Test
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => restartConnectorMutation.mutate(account.id)}
                      isLoading={restartConnectorMutation.isPending && restartConnectorMutation.variables === account.id}
                      icon={<ScanSearch size={16} />}
                    >
                      Restart connector
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() =>
                        confirm({
                          title: 'Delete WhatsApp account?',
                          message: `Delete "${account.display_name}"? Janus stops its connector and deletes its synced conversations and messages.`,
                          confirmLabel: 'Delete',
                          tone: 'warning',
                          onConfirm: () => deleteMutation.mutate(account.id),
                        })
                      }
                      isLoading={deleteMutation.isPending && deleteMutation.variables === account.id}
                      aria-label={`Delete WhatsApp account "${account.display_name}"`}
                      icon={<Trash2 size={18} className="text-gray-400 hover:text-accent" />}
                    />
                  </div>
                </div>

                {result ? <ResultBanner result={result} /> : null}
              </Card>
            );
          })
        ) : (
          <div className="rounded-2xl border border-dashed border-gray-200 bg-white p-12 text-center dark:border-gray-700 dark:bg-dark-card">
            <p className="text-sm text-gray-500 dark:text-gray-400">No WhatsApp accounts configured yet.</p>
          </div>
        )}
      </div>

      <Modal
        isOpen={editingAccountId !== null}
        onClose={() => setEditingAccountId(null)}
        title="Edit WhatsApp Account"
        className="max-w-lg"
      >
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (editingAccountId === null) {
              return;
            }
            updateMutation.mutate({ id: editingAccountId, data: editForm });
          }}
        >
          <Input
            label="Display Name"
            required
            value={editForm.display_name}
            onChange={(event) => setEditForm((current) => ({ ...current, display_name: event.target.value }))}
            placeholder="Personal WhatsApp"
          />
          <div className="flex gap-3">
            <Button type="submit" className="flex-1" isLoading={updateMutation.isPending}>
              Save Changes
            </Button>
            <Button type="button" variant="secondary" onClick={() => setEditingAccountId(null)}>
              Cancel
            </Button>
          </div>
        </form>
      </Modal>
      {dialog}
    </div>
  );
};
