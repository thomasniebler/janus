import React, { useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, Calendar, Check, RefreshCw, Save } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Card, CardContent, CardHeader } from '../../ui/Card';
import { Button } from '../../ui/Button';
import { Badge } from '../../ui/Badge';
import type { GoogleCalendarOption, PlannerAccount } from '../../../types';

interface CalendarSourceCardProps {
  title: string;
  /** Whether the user connected this provider on Settings -> Connections. */
  connected: boolean;
  /** Whether the server has the provider's OAuth client configured. */
  configured: boolean;
  queryKey: string;
  loadCalendars: () => Promise<GoogleCalendarOption[]>;
  saveCalendars: (calendarIds: string[]) => Promise<GoogleCalendarOption[]>;
  /** The planner sources this provider backs, for the last sync time and error. */
  accounts: PlannerAccount[];
}

const requestErrorMessage = (error: unknown, fallback: string) =>
  axios.isAxiosError(error) ? ((error.response?.data as { detail?: string })?.detail ?? error.message) : fallback;


const NotConnectedHint: React.FC<{ title: string; configured: boolean }> = ({ title, configured }) => (
  <p className="text-gray-500 dark:text-gray-400">
    {configured
      ? `Connect ${title.split(' ')[0]} to choose calendars to sync.`
      : `${title.split(' ')[0]} is not set up on this server yet.`}{' '}
    <Link to="/settings?tab=connections&sub=calendar" className="text-primary underline">
      Open Connections
    </Link>
  </p>
);

interface CalendarChecklistProps {
  calendars: GoogleCalendarOption[];
  idPrefix: string;
  isChecked: (calendar: GoogleCalendarOption) => boolean;
  onToggle: (calendar: GoogleCalendarOption) => void;
}

const CalendarChecklist: React.FC<CalendarChecklistProps> = ({ calendars, idPrefix, isChecked, onToggle }) => {
  if (calendars.length === 0) return <p className="text-gray-500">No calendars found.</p>;
  return (
    <ul className="space-y-1">
      {calendars.map((calendar) => (
        <li key={calendar.id} className="flex items-center gap-2">
          <input
            type="checkbox"
            id={`${idPrefix}-${calendar.id}`}
            checked={isChecked(calendar)}
            onChange={() => onToggle(calendar)}
            className="accent-primary"
          />
          <label htmlFor={`${idPrefix}-${calendar.id}`} className="flex items-center gap-2">
            {calendar.summary}
            {calendar.primary && (
              <Badge variant="secondary" className="text-xs">
                Primary
              </Badge>
            )}
            {calendar.access_role !== 'owner' && (
              <Badge variant="secondary" className="text-xs">
                Shared ({calendar.access_role})
              </Badge>
            )}
          </label>
        </li>
      ))}
    </ul>
  );
};

/** The last sync time and the most recent sync error of a provider's planner sources. */
const SyncStatus: React.FC<{ connected: boolean; accounts: PlannerAccount[] }> = ({ connected, accounts }) => {
  const syncError = accounts.find((account) => account.last_sync_error)?.last_sync_error;
  const lastSync = accounts
    .map((account) => account.last_sync_at)
    .filter((value): value is string => Boolean(value))
    .sort()
    .at(-1);
  return (
    <>
      {connected && lastSync && (
        <p className="text-xs text-gray-500 dark:text-gray-400">Last synced {new Date(lastSync).toLocaleString()}</p>
      )}
      {syncError && (
        <p role="alert" className="flex items-center gap-1 text-red-600 dark:text-red-400">
          <AlertCircle size={14} /> Sync error: {syncError}
        </p>
      )}
    </>
  );
};

/** One provider's calendars as checkboxes: tick the ones Janus should sync into the planner. */
export const CalendarSourceCard: React.FC<CalendarSourceCardProps> = ({
  title,
  connected,
  configured,
  queryKey,
  loadCalendars,
  saveCalendars,
  accounts,
}) => {
  const queryClient = useQueryClient();
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const [message, setMessage] = useState<{ status: 'success' | 'error'; text: string } | null>(null);

  const { data: calendars, isLoading, error } = useQuery({
    queryKey: [queryKey],
    queryFn: loadCalendars,
    enabled: connected,
    retry: false,
  });

  const isChecked = (calendar: GoogleCalendarOption) => overrides[calendar.id] ?? calendar.selected;
  const dirty = Object.keys(overrides).length > 0;
  const checkedIds = (calendars ?? []).filter(isChecked).map((calendar) => calendar.id);

  const save = useMutation({
    mutationFn: () => saveCalendars(checkedIds),
    onSuccess: (saved) => {
      queryClient.setQueryData([queryKey], saved);
      setOverrides({});
      setMessage({ status: 'success', text: 'Calendar selection saved. It syncs on the next sync.' });
      queryClient.invalidateQueries({ queryKey: ['planner-accounts'] });
    },
    onError: (e) => setMessage({ status: 'error', text: requestErrorMessage(e, 'Failed to save the calendar selection.') }),
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-bold flex items-center gap-2">
            <Calendar size={20} className="text-primary" />
            {title}
          </h2>
          {connected ? <Badge variant="success">Connected</Badge> : <Badge variant="secondary">Not connected</Badge>}
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {!connected && <NotConnectedHint title={title} configured={configured} />}
        {connected && isLoading && <RefreshCw size={16} className="animate-spin text-primary" />}
        {connected && error && (
          <p role="alert" className="text-red-600 dark:text-red-400">
            {requestErrorMessage(error, 'Could not load the calendars.')}
          </p>
        )}
        {calendars && (
          <CalendarChecklist
            calendars={calendars}
            idPrefix={queryKey}
            isChecked={isChecked}
            onToggle={(calendar) => setOverrides((prev) => ({ ...prev, [calendar.id]: !isChecked(calendar) }))}
          />
        )}
        {calendars && calendars.length > 0 && (
          <Button
            variant="secondary"
            onClick={() => save.mutate()}
            isLoading={save.isPending}
            disabled={!dirty || checkedIds.length === 0}
            icon={<Save size={16} />}
          >
            Save selection
          </Button>
        )}
        <SyncStatus connected={connected} accounts={accounts} />
        {message && (
          <p
            role="status"
            className={`flex items-center gap-1 ${message.status === 'success' ? 'text-green-600' : 'text-red-600'}`}
          >
            {message.status === 'success' ? <Check size={14} /> : <AlertCircle size={14} />}
            {message.text}
          </p>
        )}
      </CardContent>
    </Card>
  );
};
