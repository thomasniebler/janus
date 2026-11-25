import React, { useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, CheckCircle2, KeyRound, Link2, RefreshCw, Unlink } from 'lucide-react';
import { authApi, connectionsApi } from '../../api';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
import type { ConnectionProvider, ConnectionStatus } from '../../types';
import { TelegramConfig } from './TelegramConfig';

/** The backend's `detail` for a failed request, else the request's own error, else `fallback`. */
const requestErrorMessage = (error: unknown, fallback: string) =>
  axios.isAxiosError(error) ? ((error.response?.data as { detail?: string })?.detail ?? error.message) : fallback;

const PROVIDER_COPY: Record<ConnectionProvider, { title: string; blurb: string; uses: string }> = {
  google: {
    title: 'Google',
    blurb: 'Google Calendar, Google Tasks and Gmail.',
    uses: 'Calendars and tasks appear on the Planner page, Gmail on the Mail page.',
  },
  microsoft: {
    title: 'Microsoft',
    blurb: 'Outlook calendars and Outlook / Microsoft 365 mail.',
    uses: 'Calendars appear on the Planner page, Outlook mail on the Mail page.',
  },
};

interface ProviderCardProps {
  status: ConnectionStatus;
  signInLinked?: boolean;
  onFeedback: (feedback: { status: 'success' | 'error'; message: string }) => void;
}

const ProviderCard: React.FC<ProviderCardProps> = ({ status, signInLinked, onFeedback }) => {
  const queryClient = useQueryClient();
  const copy = PROVIDER_COPY[status.provider];

  const connectMutation = useMutation({
    mutationFn: async () => (await connectionsApi.getAuthUrl(status.provider)).data.url,
    // Hand the browser to the provider's consent screen; it returns to Settings -> Connections.
    onSuccess: (url) => {
      window.location.href = url;
    },
    onError: (error) =>
      onFeedback({ status: 'error', message: requestErrorMessage(error, `Could not start the ${copy.title} sign-in.`) }),
  });

  const disconnectMutation = useMutation({
    mutationFn: async () => {
      await connectionsApi.disconnect(status.provider);
      // Sign-In linking is separate from the connection and may already be absent.
      if (status.provider === 'google') await Promise.allSettled([authApi.unlinkGoogle()]);
    },
    onSuccess: () => {
      for (const key of ['connections', 'planner-accounts', 'me', 'email-accounts']) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
      onFeedback({ status: 'success', message: `${copy.title} disconnected.` });
    },
    onError: (error) =>
      onFeedback({ status: 'error', message: requestErrorMessage(error, `Failed to disconnect ${copy.title}.`) }),
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div className="space-y-1">
            <h3 className="text-lg font-bold">{copy.title}</h3>
            <p className="text-sm text-gray-500 dark:text-gray-400">{copy.blurb}</p>
          </div>
          {status.connected && (
            <Badge variant="success" className="shrink-0">
              Connected
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {status.connected && (
          <div className="rounded-xl border border-green-200 bg-green-50 p-4 text-sm text-green-800 dark:border-green-900/50 dark:bg-green-900/20 dark:text-green-200 space-y-1">
            <div className="flex items-center gap-2 font-bold">
              <CheckCircle2 size={16} />
              {status.account_email ? `Connected as ${status.account_email}` : `${copy.title} is connected`}
            </div>
            <p>{copy.uses}</p>
            {status.mail_supported && !status.mail_ready && <p>Mail access was not granted. Reconnect to add a mailbox.</p>}
            {status.provider === 'google' && signInLinked !== undefined && (
              <p>Sign in with Google: {signInLinked ? 'linked' : 'not linked'}</p>
            )}
          </div>
        )}

        {!status.configured && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-200 space-y-2">
            <div className="flex items-center gap-2 font-bold">
              <KeyRound size={16} />
              {copy.title} is not set up on this server
            </div>
            <p>
              Add {status.config_keys.map((key) => (
                <code key={key} className="mx-0.5 rounded bg-white/60 px-1 py-0.5 text-xs dark:bg-black/30">
                  {key}
                </code>
              ))}{' '}
              to <code className="text-xs">~/.config/janus/settings.toml</code> and restart Janus. Register these redirect URIs
              with the provider:
            </p>
            <ul className="space-y-1">
              {status.redirect_uris.map((uri) => (
                <li key={uri}>
                  <code className="rounded bg-white/60 px-1 py-0.5 text-xs dark:bg-black/30">{uri}</code>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-3">
          {status.connected ? (
            <>
              <Button
                variant="secondary"
                onClick={() => connectMutation.mutate()}
                isLoading={connectMutation.isPending}
                disabled={!status.configured}
                icon={<RefreshCw size={16} />}
              >
                Reconnect
              </Button>
              <Button
                variant="danger"
                onClick={() => disconnectMutation.mutate()}
                isLoading={disconnectMutation.isPending}
                icon={<Unlink size={16} />}
              >
                Disconnect {copy.title}
              </Button>
            </>
          ) : (
            <Button
              onClick={() => connectMutation.mutate()}
              isLoading={connectMutation.isPending}
              disabled={!status.configured}
              icon={<Link2 size={16} />}
            >
              Connect {copy.title}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
};

/** Settings -> Connections: one "Connect" button per provider; calendars and mailboxes are chosen on their pages. */
export const ConnectionsConfig: React.FC = () => {
  const [feedback, setFeedback] = useState<{ status: 'success' | 'error'; message: string } | null>(null);

  const { data: overview, isLoading } = useQuery({
    queryKey: ['connections'],
    queryFn: async () => (await connectionsApi.getOverview()).data,
  });
  const { data: currentUser } = useQuery({
    queryKey: ['me'],
    queryFn: async () => (await authApi.getMe()).data,
  });

  if (isLoading || !overview) {
    return (
      <Card>
        <CardContent className="h-32 flex items-center justify-center">
          <RefreshCw className="animate-spin text-primary" />
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-gray-500 dark:text-gray-400">
        Connect your accounts once. Then pick the calendars to sync on the Planner page and add mailboxes on the Mail
        page.
      </p>
      <ProviderCard status={overview.google} signInLinked={currentUser?.google_linked ?? false} onFeedback={setFeedback} />
      <ProviderCard status={overview.microsoft} onFeedback={setFeedback} />
      <TelegramConfig />
      {feedback && (
        <div
          role="status"
          className={`p-4 rounded-2xl text-sm flex items-center gap-3 ${
            feedback.status === 'success'
              ? 'bg-green-50 text-green-600 dark:bg-green-900/20 dark:text-green-400'
              : 'bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400'
          }`}
        >
          {feedback.status === 'success' ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
          {feedback.message}
        </div>
      )}
    </div>
  );
};
