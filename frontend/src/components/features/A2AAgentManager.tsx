import React, { useState } from 'react';
import { Bot, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { a2aApi } from '../../a2aApi';
import type { A2AAgent } from '../../types';
import { Button } from '../ui/Button';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Input } from '../ui/Input';

const EMPTY_FORM = { name: '', base_url: '', token: '' };

const errorMessage = (error: unknown): string => {
  if (error && typeof error === 'object' && 'response' in error) {
    const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
    if (typeof detail === 'string') return detail;
  }
  return 'The remote agent operation could not be completed.';
};

const AgentRow: React.FC<{ agent: A2AAgent; onChanged: () => void }> = ({ agent, onChanged }) => {
  const toggle = useMutation({ mutationFn: () => a2aApi.update(agent.id, { enabled: !agent.enabled }), onSuccess: onChanged });
  const refresh = useMutation({ mutationFn: () => a2aApi.refresh(agent.id), onSuccess: onChanged });
  const remove = useMutation({ mutationFn: () => a2aApi.remove(agent.id), onSuccess: onChanged });
  const error = toggle.error ?? refresh.error ?? remove.error;
  return (
    <li className="space-y-2 rounded-lg border border-white/10 p-3" data-testid={`a2a-agent-${agent.id}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium">{agent.name}</p>
          <p className="truncate text-xs text-gray-500">{agent.base_url}</p>
          {agent.rpc_url ? (
            <p className="text-xs text-gray-400">
              Card: {agent.card_name}
              {agent.protocol_version ? ` · A2A ${agent.protocol_version}` : ''}
              {agent.token_configured ? ' · bearer token saved' : ''}
            </p>
          ) : (
            <p className="text-xs text-amber-400">No agent card yet{agent.last_error ? `: ${agent.last_error}` : ''}</p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant={agent.enabled ? 'secondary' : 'primary'} disabled={!agent.rpc_url} isLoading={toggle.isPending} onClick={() => toggle.mutate()}>
            {agent.enabled ? 'Switch off' : 'Switch on'}
          </Button>
          <Button size="sm" variant="ghost" icon={<RefreshCw size={14} />} isLoading={refresh.isPending} onClick={() => refresh.mutate()}>Refresh card</Button>
          <Button size="sm" variant="ghost" icon={<Trash2 size={14} />} isLoading={remove.isPending} onClick={() => remove.mutate()} aria-label={`Remove ${agent.name}`}>Remove</Button>
        </div>
      </div>
      {agent.skills.length > 0 && (
        <details className="text-xs text-gray-400">
          <summary>Skills the agent advertises (its own description, unverified)</summary>
          <ul className="mt-1 list-disc pl-5">
            {agent.skills.map((skill) => <li key={skill.name}><span className="text-gray-300">{skill.name}</span>{skill.description ? `: ${skill.description}` : ''}</li>)}
          </ul>
        </details>
      )}
      {error && <p className="text-sm text-red-400">{errorMessage(error)}</p>}
    </li>
  );
};

/** Remote A2A agents Janus may ask for help from chat (JAN-404). */
export const A2AAgentManager: React.FC = () => {
  const queryClient = useQueryClient();
  const [form, setForm] = useState(EMPTY_FORM);
  const [showForm, setShowForm] = useState(false);
  const { data, isLoading, isError } = useQuery({ queryKey: ['a2a-agents'], queryFn: async () => (await a2aApi.list()).data });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['a2a-agents'] });
  const create = useMutation({
    mutationFn: () => a2aApi.create({ name: form.name.trim(), base_url: form.base_url.trim(), token: form.token.trim() || undefined }),
    onSuccess: () => { setForm(EMPTY_FORM); setShowForm(false); refresh(); },
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="flex items-center gap-2 text-lg font-bold"><Bot size={18} /> Remote agents</h2>
            <p className="mt-1 text-sm text-gray-400">
              Agents outside Janus that speak the Agent2Agent (A2A) protocol. Once switched on, Janus can ask them for help in chat; you approve every prompt before it is sent.
            </p>
          </div>
          <Button size="sm" icon={<Plus size={15} />} onClick={() => setShowForm((current) => !current)}>Add remote agent</Button>
        </div>
      </CardHeader>
      {showForm && (
        <CardContent className="border-b border-white/10">
          <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}>
            <div className="grid gap-4 md:grid-cols-2">
              <Input label="Name" required maxLength={80} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
              <Input label="Agent URL" required type="url" placeholder="https://agent.example.com" value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} />
            </div>
            <Input label="Bearer token (optional)" type="password" autoComplete="off" value={form.token} onChange={(event) => setForm({ ...form, token: event.target.value })} />
            <div className="flex gap-2">
              <Button type="submit" size="sm" isLoading={create.isPending} disabled={!form.name.trim() || !form.base_url.trim()}>Add and fetch card</Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setShowForm(false)}>Cancel</Button>
            </div>
            {create.isError && <p className="text-sm text-red-400">{errorMessage(create.error)}</p>}
          </form>
        </CardContent>
      )}
      <CardContent>
        {isLoading && <p className="text-sm text-gray-400">Loading remote agents…</p>}
        {isError && <p className="text-sm text-red-400">Remote agents are unavailable.</p>}
        {data && data.length === 0 && <p className="text-sm text-gray-500">No remote agents yet.</p>}
        {data && data.length > 0 && <ul className="space-y-3">{data.map((agent) => <AgentRow key={agent.id} agent={agent} onChanged={refresh} />)}</ul>}
      </CardContent>
    </Card>
  );
};
