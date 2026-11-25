import React, { useState } from 'react';
import { KeyRound, Plus, Trash2, Webhook as WebhookIcon } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { webhooksApi } from '../../../webhooksApi';
import type { Webhook, WebhookSecret } from '../../../types';
import { Button } from '../../ui/Button';
import { Card, CardContent, CardHeader } from '../../ui/Card';
import { Input } from '../../ui/Input';

const errorMessage = (error: unknown): string => {
  if (error && typeof error === 'object' && 'response' in error) {
    const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
    if (typeof detail === 'string') return detail;
  }
  return 'The webhook operation could not be completed.';
};

/** Shows a just-created URL and/or secret, the only time they are visible. */
const SecretNotice: React.FC<{ shown: WebhookSecret; onDone: () => void }> = ({ shown, onDone }) => (
  <div className="space-y-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm" role="status" data-testid="webhook-secret">
    <p className="font-medium">Copy these now: Janus will not show them again.</p>
    {shown.path && (
      <p>
        URL: <code className="break-all" data-testid="webhook-url">{`${window.location.origin}${shown.path}`}</code>
      </p>
    )}
    <p>
      Secret: <code className="break-all" data-testid="webhook-secret-value">{shown.secret}</code>
    </p>
    <p className="text-xs text-gray-400">
      Sign each request with this secret: <code>X-Hub-Signature-256: sha256=HMAC(body)</code> (GitHub style), or{' '}
      <code>X-Janus-Timestamp</code> plus <code>X-Janus-Signature: sha256=HMAC(timestamp + "." + body)</code>.
    </p>
    <Button size="sm" variant="secondary" onClick={onDone}>Done</Button>
  </div>
);

const WebhookRow: React.FC<{ webhook: Webhook; onChanged: () => void; onSecret: (shown: WebhookSecret) => void }> = ({ webhook, onChanged, onSecret }) => {
  const toggle = useMutation({ mutationFn: () => webhooksApi.update(webhook.id, { enabled: !webhook.enabled }), onSuccess: onChanged });
  const rotate = useMutation({ mutationFn: () => webhooksApi.rotateSecret(webhook.id), onSuccess: (response) => { onSecret(response.data); onChanged(); } });
  const remove = useMutation({ mutationFn: () => webhooksApi.remove(webhook.id), onSuccess: onChanged });
  const error = toggle.error ?? rotate.error ?? remove.error;
  return (
    <li className="space-y-2 rounded-lg border border-white/10 p-3" data-testid={`webhook-${webhook.id}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-medium">{webhook.name}</p>
          <p className="text-xs text-gray-500">
            /api/webhooks/in/{webhook.key_prefix}… · {webhook.received_count} {webhook.received_count === 1 ? 'delivery' : 'deliveries'}
            {webhook.enabled ? '' : ' · switched off'}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant={webhook.enabled ? 'secondary' : 'primary'} isLoading={toggle.isPending} onClick={() => toggle.mutate()}>
            {webhook.enabled ? 'Switch off' : 'Switch on'}
          </Button>
          <Button size="sm" variant="ghost" icon={<KeyRound size={14} />} isLoading={rotate.isPending} onClick={() => rotate.mutate()}>New secret</Button>
          <Button size="sm" variant="ghost" icon={<Trash2 size={14} />} isLoading={remove.isPending} onClick={() => remove.mutate()} aria-label={`Delete ${webhook.name}`}>Delete</Button>
        </div>
      </div>
      {error && <p className="text-sm text-red-400">{errorMessage(error)}</p>}
    </li>
  );
};

/** Inbound webhooks (JAN-406): URLs outside services POST to, which start "Webhook received" triggers. */
export const WebhookManager: React.FC = () => {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [shown, setShown] = useState<WebhookSecret | null>(null);
  const { data, isLoading, isError } = useQuery({ queryKey: ['webhooks'], queryFn: async () => (await webhooksApi.list()).data });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['webhooks'] });
  const create = useMutation({
    mutationFn: () => webhooksApi.create({ name: name.trim() }),
    onSuccess: (response) => { setName(''); setShown(response.data); refresh(); },
  });

  return (
    <Card>
      <CardHeader>
        <h2 className="flex items-center gap-2 text-lg font-bold"><WebhookIcon size={18} /> Webhooks</h2>
        <p className="mt-1 text-sm text-gray-400">
          A URL that outside services (GitHub, a CI system, a home server) can send signed JSON to. Each delivery fires the
          <em> Webhook received</em> event, so a trigger on that event can react to it.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}>
          <div className="min-w-48 flex-1">
            <Input label="Webhook name" maxLength={80} value={name} onChange={(event) => setName(event.target.value)} placeholder="GitHub pushes" />
          </div>
          <Button type="submit" size="sm" icon={<Plus size={15} />} disabled={!name.trim()} isLoading={create.isPending}>Create webhook</Button>
        </form>
        {create.isError && <p className="text-sm text-red-400">{errorMessage(create.error)}</p>}
        {shown && <SecretNotice shown={shown} onDone={() => setShown(null)} />}
        {isLoading && <p className="text-sm text-gray-400">Loading webhooks…</p>}
        {isError && <p className="text-sm text-red-400">Webhooks are unavailable.</p>}
        {data && data.length === 0 && <p className="text-sm text-gray-500">No webhooks yet.</p>}
        {data && data.length > 0 && (
          <ul className="space-y-3">
            {data.map((webhook) => <WebhookRow key={webhook.id} webhook={webhook} onChanged={refresh} onSecret={setShown} />)}
          </ul>
        )}
      </CardContent>
    </Card>
  );
};
