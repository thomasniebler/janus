import React, { useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, Check, Copy, KeyRound, RefreshCw, Trash2 } from 'lucide-react';
import { inboxApi, type DeviceToken, type DeviceTokenCreated } from '../../lib/inboxApi';
import { Button } from '../ui/Button';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Input } from '../ui/Input';

const tokensQueryKey = ['inbox', 'tokens'] as const;

const errorMessage = (error: unknown): string => {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    return typeof detail === 'string' ? detail : error.message;
  }
  return 'Something went wrong.';
};

/** The API sends an explicit UTC offset, so the value parses to the right instant in any zone. */
const formatWhen = (value: string | null): string => (value ? new Date(value).toLocaleString() : 'never');

const deviceAccessHeader = (
  <CardHeader>
    <div className="space-y-2">
      <h2 className="text-xl font-bold flex items-center gap-2">
        <KeyRound size={24} className="text-primary" />
        Desktop and device access
      </h2>
      <p className="text-sm text-gray-500 dark:text-gray-400">
        A device token lets a script or device (a desktop popup, a phone) read your action inbox and dismiss or mark
        read what is in it. It cannot reach anything else in Janus, and approvals stay in the web app. Create one token
        per device, so a lost device is one revocation.
      </p>
    </div>
  </CardHeader>
);

const TokenRow: React.FC<{ token: DeviceToken; onRevoke: (id: number) => void; busy: boolean }> = ({
  token,
  onRevoke,
  busy,
}) => (
  <li className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-gray-200 p-3 dark:border-gray-700">
    <div className="min-w-0">
      <p className="font-bold truncate">{token.name}</p>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        <code>{token.token_prefix}…</code> · created {formatWhen(token.created_at)} · last used{' '}
        {formatWhen(token.last_used_at)}
      </p>
    </div>
    <Button variant="ghost" onClick={() => onRevoke(token.id)} disabled={busy} aria-label={`Revoke ${token.name}`}>
      <Trash2 size={16} /> Revoke
    </Button>
  </li>
);

export const DeviceAccessSettings: React.FC = () => {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [created, setCreated] = useState<DeviceTokenCreated | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  const {
    data: tokens,
    isLoading,
    isError,
  } = useQuery({
    queryKey: tokensQueryKey,
    queryFn: async () => (await inboxApi.listTokens()).data,
  });

  const createMutation = useMutation({
    mutationFn: async () => (await inboxApi.createToken(name.trim())).data,
    onSuccess: (token) => {
      setCreated(token);
      setName('');
      setFeedback(null);
      void queryClient.invalidateQueries({ queryKey: tokensQueryKey });
    },
    onError: (error) => setFeedback(errorMessage(error)),
  });

  const revokeMutation = useMutation({
    mutationFn: async (id: number) => inboxApi.revokeToken(id),
    onSuccess: (_response, id) => {
      // Keep a just-created token's one-time secret on screen unless that very token was revoked.
      setCreated((current) => (current?.id === id ? null : current));
      setFeedback(null);
      void queryClient.invalidateQueries({ queryKey: tokensQueryKey });
    },
    onError: (error) => setFeedback(errorMessage(error)),
  });

  if (isError) {
    return (
      <Card>
        {deviceAccessHeader}
        <CardContent>
          <div
            role="alert"
            className="p-4 rounded-2xl text-sm flex items-center gap-3 bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400"
          >
            <AlertCircle size={20} />
            <p className="font-medium">Could not load your device tokens.</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (isLoading || !tokens) {
    return (
      <Card>
        {deviceAccessHeader}
        <CardContent className="h-32 flex items-center justify-center">
          <RefreshCw className="animate-spin text-primary" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      {deviceAccessHeader}
      <CardContent className="space-y-6">
        {created && (
          <div
            role="status"
            className="rounded-2xl border border-green-300 bg-green-50 p-4 text-sm dark:border-green-700 dark:bg-green-900/20"
          >
            <p className="flex items-center gap-2 font-bold text-green-700 dark:text-green-300">
              <Check size={16} /> Token created for {created.name}. Copy it now: it is shown only once.
            </p>
            <div className="mt-3 flex gap-2">
              <Input
                aria-label="New device token"
                readOnly
                value={created.token}
                onFocus={(event) => event.target.select()}
              />
              <Button
                variant="ghost"
                onClick={() => void navigator.clipboard?.writeText(created.token)}
                aria-label="Copy token"
              >
                <Copy size={16} /> Copy
              </Button>
            </div>
          </div>
        )}

        {tokens.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-gray-400">No device tokens yet.</p>
        ) : (
          <ul aria-label="Device tokens" className="space-y-2">
            {tokens.map((token) => (
              <TokenRow
                key={token.id}
                token={token}
                onRevoke={(id) => revokeMutation.mutate(id)}
                busy={revokeMutation.isPending}
              />
            ))}
          </ul>
        )}

        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (name.trim()) createMutation.mutate();
          }}
        >
          <div className="min-w-[14rem] flex-1">
            <Input
              label="Device name"
              aria-label="Device name"
              placeholder="Laptop eww popup"
              maxLength={80}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <Button type="submit" isLoading={createMutation.isPending} disabled={!name.trim()}>
            Create token
          </Button>
        </form>

        {feedback && (
          <div
            role="alert"
            className="p-4 rounded-2xl text-sm flex items-center gap-3 bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400"
          >
            <AlertCircle size={20} />
            <p className="font-medium">{feedback}</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
};
