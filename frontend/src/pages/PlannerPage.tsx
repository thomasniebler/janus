import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, CheckCircle2, RefreshCw } from 'lucide-react';
import { connectionsApi, plannerApi } from '../api';
import { Button } from '../components/ui/Button';
import { CalendarSourceCard } from '../components/features/planner/CalendarSourceCard';
import { PlannerEvents, PlannerTasks } from '../components/features/planner/LocalPlannerItems';
import type { ConnectionProvider, ConnectionStatus, PlannerAccount } from '../types';

const SYNC_SOURCES = new Set(['google_calendar', 'google_tasks', 'microsoft_calendar']);

const SyncBanner: React.FC<{ ok: boolean; text: string }> = ({ ok, text }) => (
  <div
    role="status"
    className={`flex items-center gap-2 rounded-xl px-4 py-3 text-sm ${
      ok
        ? 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-300'
        : 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-300'
    }`}
  >
    {ok ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
    {text}
  </div>
);

interface ProviderCalendarsProps {
  provider: ConnectionProvider;
  title: string;
  queryKey: string;
  status?: ConnectionStatus;
  accounts: PlannerAccount[];
}

/** One provider's calendar card, wired to its connection status and calendar routes. */
const ProviderCalendars: React.FC<ProviderCalendarsProps> = ({ provider, title, queryKey, status, accounts }) => (
  <CalendarSourceCard
    title={title}
    connected={status?.connected ?? false}
    configured={status?.configured ?? false}
    queryKey={queryKey}
    loadCalendars={async () => (await connectionsApi.getCalendars(provider)).data}
    saveCalendars={async (ids) => (await connectionsApi.updateCalendars(provider, ids)).data}
    accounts={accounts}
  />
);

/** The Planner: pick which Google and Outlook calendars sync, sync now, and manage local events and tasks. */
const PlannerPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [syncMessage, setSyncMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const { data: overview } = useQuery({
    queryKey: ['connections'],
    queryFn: async () => (await connectionsApi.getOverview()).data,
  });
  const { data: accounts } = useQuery({
    queryKey: ['planner-accounts'],
    queryFn: async () => (await plannerApi.getAccounts()).data,
  });

  const sync = useMutation({
    mutationFn: async () => (await plannerApi.syncNow()).data,
    onSuccess: async (result) => {
      setSyncMessage(
        result.errors.length > 0
          ? { ok: false, text: result.errors.join(' ') }
          : { ok: true, text: `Synced ${result.synced_accounts} source${result.synced_accounts === 1 ? '' : 's'}.` },
      );
      await Promise.all(
        [['planner'], ['planner-accounts'], ['dashboard', 'schedule'], ['dashboard', 'tasks']].map((queryKey) =>
          queryClient.invalidateQueries({ queryKey }),
        ),
      );
    },
    onError: () => setSyncMessage({ ok: false, text: 'The sync failed. Check the server log.' }),
  });

  const sources = (accounts ?? []).filter((account) => SYNC_SOURCES.has(account.provider));
  const googleAccounts = sources.filter((account) => account.provider.startsWith('google'));
  const outlookAccounts = sources.filter((account) => account.provider === 'microsoft_calendar');

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Planner</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Calendars from Google and Outlook, plus your own events and tasks.
          </p>
        </div>
        <Button
          variant="secondary"
          onClick={() => sync.mutate()}
          isLoading={sync.isPending}
          disabled={!overview?.google.connected && !overview?.microsoft.connected}
          icon={<RefreshCw size={16} />}
        >
          Sync now
        </Button>
      </div>
      {syncMessage && <SyncBanner ok={syncMessage.ok} text={syncMessage.text} />}

      <div className="grid gap-6 lg:grid-cols-2">
        <ProviderCalendars
          provider="google"
          title="Google Calendar"
          queryKey="google-calendars"
          status={overview?.google}
          accounts={googleAccounts}
        />
        <ProviderCalendars
          provider="microsoft"
          title="Outlook Calendar"
          queryKey="outlook-calendars"
          status={overview?.microsoft}
          accounts={outlookAccounts}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <PlannerEvents />
        <PlannerTasks />
      </div>
    </div>
  );
};

export default PlannerPage;
