import React, { useState } from 'react';
import { Send } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { telegramApi } from '../../telegramApi';
import { Button } from '../ui/Button';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Input } from '../ui/Input';

const errorMessage = (error: unknown): string => {
  if (error && typeof error === 'object' && 'response' in error) {
    const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
    if (typeof detail === 'string') return detail;
  }
  return 'The Telegram request could not be completed.';
};

/** Settings -> Connections -> Messaging -> Telegram: your own bot, linked to your own chat (JAN-405). */
export const TelegramConfig: React.FC = () => {
  const queryClient = useQueryClient();
  const [token, setToken] = useState('');
  const [tested, setTested] = useState(false);
  const { data: status } = useQuery({
    queryKey: ['telegram'],
    queryFn: async () => (await telegramApi.status()).data,
    // While waiting for the /start message, look for the pairing every few seconds.
    refetchInterval: (query) => (query.state.data?.connected && !query.state.data.linked ? 3000 : false),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['telegram'] });
  const connect = useMutation({ mutationFn: () => telegramApi.connect(token.trim()), onSuccess: () => { setToken(''); refresh(); } });
  const sendTest = useMutation({ mutationFn: () => telegramApi.sendTest(), onSuccess: () => setTested(true) });
  const disconnect = useMutation({ mutationFn: () => telegramApi.disconnect(), onSuccess: () => { setTested(false); refresh(); } });
  const error = connect.error ?? sendTest.error ?? disconnect.error;

  return (
    <Card data-testid="telegram-config">
      <CardHeader>
        <h3 className="flex items-center gap-2 font-bold"><Send size={16} /> Telegram</h3>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Connect your own bot (create one with @BotFather). Messages you send it can start Telegram triggers, and trigger
          runs can message you there.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {status?.connected ? (
          <>
            <p className="text-sm">
              Bot <span className="font-mono">@{status.bot_username}</span> ·{' '}
              {status.linked ? <span className="text-green-500">linked to your chat</span> : 'waiting for your chat'}
            </p>
            {!status.linked && status.pairing_code && (
              <p className="text-sm" data-testid="telegram-pairing">
                Open @{status.bot_username} in Telegram and send <code className="font-mono">/start {status.pairing_code}</code>.
              </p>
            )}
            {status.last_error && <p className="text-sm text-amber-400">Last problem: {status.last_error}</p>}
            <div className="flex flex-wrap gap-2">
              {status.linked && (
                <Button size="sm" variant="secondary" isLoading={sendTest.isPending} onClick={() => sendTest.mutate()}>Send test message</Button>
              )}
              <Button size="sm" variant="ghost" isLoading={disconnect.isPending} onClick={() => disconnect.mutate()}>Disconnect</Button>
            </div>
            {tested && <p className="text-sm text-green-500">Test message sent.</p>}
          </>
        ) : (
          <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => { event.preventDefault(); connect.mutate(); }}>
            <div className="min-w-64 flex-1">
              <Input label="Bot token" type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} placeholder="123456789:AA..." />
            </div>
            <Button type="submit" size="sm" disabled={!token.trim()} isLoading={connect.isPending}>Connect</Button>
          </form>
        )}
        {error && <p className="text-sm text-red-400">{errorMessage(error)}</p>}
      </CardContent>
    </Card>
  );
};
