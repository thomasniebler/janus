import React, { useState } from 'react';
import { Check, ChevronDown, ChevronUp, ExternalLink, KeyRound, Link, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { mcpApi } from '../../api';
import type { MCPServer } from '../../types';
import { Button } from '../ui/Button';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Input } from '../ui/Input';
import { A2AAgentManager } from './A2AAgentManager';
import { InjectionClassifierCard } from './InjectionClassifierCard';
import { ToolPermissions } from './ToolPermissions';

type FormState = {
  provider_name: string;
  endpoint: string;
  oauth_authorization_endpoint: string;
  oauth_token_endpoint: string;
  oauth_client_id: string;
  oauth_scopes: string;
  access_token: string;
};

const EMPTY_FORM: FormState = {
  provider_name: '',
  endpoint: '',
  oauth_authorization_endpoint: '',
  oauth_token_endpoint: '',
  oauth_client_id: '',
  oauth_scopes: '',
  access_token: '',
};

const errorMessage = (error: unknown): string => {
  if (error && typeof error === 'object' && 'response' in error) {
    const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
    if (typeof detail === 'string') return detail;
  }
  return 'The MCP operation could not be completed.';
};

const CREDENTIAL_LABEL: Record<MCPServer['credential_status'], string> = {
  none: 'No credential',
  oauth: 'OAuth',
  static: 'Bearer token',
};

/** Replace or remove a server's static bearer token (JAN-342); the saved token is never shown. */
const BearerTokenControls: React.FC<{ server: MCPServer; onSaved: () => void }> = ({ server, onSaved }) => {
  const [token, setToken] = useState('');
  const mutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => mcpApi.updateServer(server.id, data),
    onSuccess: () => { setToken(''); onSaved(); },
  });
  const hasToken = server.credential_status === 'static';
  return <form className="space-y-2 rounded-lg bg-black/10 p-3" onSubmit={(event) => { event.preventDefault(); mutation.mutate({ access_token: token }); }}>
    <Input label={hasToken ? 'Replace bearer token' : 'Bearer token'} type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} placeholder={hasToken ? 'Token saved, paste a new one to replace it' : 'Paste the token the server gave you'} />
    {server.credential_status === 'oauth' && <p className="text-xs text-gray-500">Saving a token replaces this server's OAuth connection.</p>}
    <div className="flex flex-wrap gap-2">
      <Button type="submit" size="sm" icon={<KeyRound size={14} />} disabled={!token.trim()} isLoading={mutation.isPending && !mutation.variables?.clear_access_token}>Save token</Button>
      {hasToken && <Button type="button" size="sm" variant="ghost" isLoading={mutation.isPending && Boolean(mutation.variables?.clear_access_token)} onClick={() => mutation.mutate({ clear_access_token: true })}>Remove token</Button>}
    </div>
    {mutation.isError && <p className="text-sm text-red-400">{errorMessage(mutation.error)}</p>}
  </form>;
};

const statusLabel = (server: MCPServer): string => {
  if (server.discovery_status === 'ready') return 'Healthy';
  if (server.discovery_status === 'auth_required') return server.credential_status === 'static' ? 'Token rejected' : 'Authorization required';
  if (server.discovery_status === 'error') return 'Discovery failed';
  return 'Not discovered';
};

export const MCPRegistryManager: React.FC = () => {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [showForm, setShowForm] = useState(false);
  const [expandedServer, setExpandedServer] = useState<number | null>(null);
  const { data, isLoading, isError } = useQuery({
    queryKey: ['mcp-registry'],
    queryFn: async () => (await mcpApi.getRegistry()).data,
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['mcp-registry'] });
  const createMutation = useMutation({
    mutationFn: () => mcpApi.createServer({
      provider_name: form.provider_name.trim(),
      endpoint: form.endpoint.trim(),
      transport: 'streamable_http',
      enabled: false,
      oauth_authorization_endpoint: form.oauth_authorization_endpoint.trim() || undefined,
      oauth_token_endpoint: form.oauth_token_endpoint.trim() || undefined,
      oauth_client_id: form.oauth_client_id.trim() || undefined,
      oauth_scopes: form.oauth_scopes.trim() || undefined,
      access_token: form.access_token.trim() || undefined,
    }),
    onSuccess: () => {
      setForm(EMPTY_FORM);
      setShowForm(false);
      refresh();
    },
  });
  const discoverMutation = useMutation({ mutationFn: (id: number) => mcpApi.discover(id), onSuccess: refresh });
  const updateMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) => mcpApi.updateServer(id, { enabled }),
    onSuccess: refresh,
  });
  const disconnectMutation = useMutation({
    mutationFn: (id: number) => mcpApi.disconnect(id),
    onSuccess: refresh,
  });
  const toolMutation = useMutation({
    mutationFn: ({ serverId, toolId, enabled }: { serverId: number; toolId: number; enabled: boolean }) =>
      mcpApi.setToolEnabled(serverId, toolId, enabled),
    onSuccess: refresh,
  });
  const oauthMutation = useMutation({
    mutationFn: (id: number) => mcpApi.startOAuth(id),
    onSuccess: (response) => { window.location.assign(response.data.authorization_url); },
  });

  if (isLoading) return <Card><CardContent><p className="text-sm text-gray-400">Loading tool registry…</p></CardContent></Card>;
  if (isError || !data) return <Card><CardContent><p className="text-sm text-red-400">The tool registry is unavailable. Apply the backend migration and release before using this panel.</p></CardContent></Card>;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-lg font-bold">Tools and Connections</h2>
              <p className="mt-1 text-sm text-gray-400">Manage built-in Janus tools and owner-scoped HTTP MCP connections.</p>
            </div>
            <Button size="sm" icon={<Plus size={15} />} onClick={() => setShowForm((current) => !current)}>Add MCP server</Button>
          </div>
        </CardHeader>
        {showForm && (
          <CardContent className="border-b border-white/10">
            <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); createMutation.mutate(); }}>
              <div className="grid gap-4 md:grid-cols-2">
                <Input label="Provider name" required value={form.provider_name} onChange={(event) => setForm({ ...form, provider_name: event.target.value })} />
                <Input label="HTTP MCP endpoint" required type="url" placeholder="https://mcp.example.com/mcp" value={form.endpoint} onChange={(event) => setForm({ ...form, endpoint: event.target.value })} />
                <Input label="OAuth authorization endpoint (optional)" type="url" value={form.oauth_authorization_endpoint} onChange={(event) => setForm({ ...form, oauth_authorization_endpoint: event.target.value })} />
                <Input label="OAuth token endpoint (optional)" type="url" value={form.oauth_token_endpoint} onChange={(event) => setForm({ ...form, oauth_token_endpoint: event.target.value })} />
                <Input label="OAuth client ID (optional)" value={form.oauth_client_id} onChange={(event) => setForm({ ...form, oauth_client_id: event.target.value })} />
                <Input label="OAuth scopes (optional)" placeholder="tools.read tools.call" value={form.oauth_scopes} onChange={(event) => setForm({ ...form, oauth_scopes: event.target.value })} />
                <Input label="Bearer token (optional)" type="password" autoComplete="off" value={form.access_token} onChange={(event) => setForm({ ...form, access_token: event.target.value })} />
              </div>
              <p className="text-xs text-gray-500">Use either OAuth or a bearer token. With OAuth you sign in at the provider's page; a bearer token is stored encrypted and never shown again.</p>
              {createMutation.isError && <p className="text-sm text-red-400">{errorMessage(createMutation.error)}</p>}
              <div className="flex gap-2"><Button type="submit" size="sm" isLoading={createMutation.isPending}>Save server</Button><Button type="button" size="sm" variant="ghost" onClick={() => setShowForm(false)}>Cancel</Button></div>
            </form>
          </CardContent>
        )}
        <CardContent>
          <ToolPermissions tools={data.builtins} />
        </CardContent>
      </Card>

      <InjectionClassifierCard />

      <Card>
        <CardHeader><div className="flex items-center gap-2"><Link size={17} /><h2 className="text-lg font-bold">Remote MCP servers</h2></div><p className="mt-1 text-sm text-gray-400">Janus can use the enabled tools of an enabled server whose discovery succeeded, in chat only. Tools the server marks read-only run without asking; every other call waits for your approval. Unattended runs never use them.</p></CardHeader>
        <CardContent className="space-y-4">
          {data.remote_servers.length === 0 && <p className="text-sm text-gray-400">No remote MCP servers are connected.</p>}
          {data.remote_servers.map((server) => {
            const expanded = expandedServer === server.id;
            return <div key={server.id} className="rounded-xl border border-white/10 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <button type="button" className="flex items-center gap-2 text-left" onClick={() => setExpandedServer(expanded ? null : server.id)}>{expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}<span className="font-semibold">{server.provider_name}</span><span className="text-xs text-gray-500">{statusLabel(server)}</span><span className="rounded-full border border-white/10 px-2 py-0.5 text-xs text-gray-400" data-testid={`mcp-credential-${server.id}`}>{CREDENTIAL_LABEL[server.credential_status] ?? server.credential_status}</span></button>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" icon={<RefreshCw size={14} />} isLoading={discoverMutation.isPending && discoverMutation.variables === server.id} onClick={() => discoverMutation.mutate(server.id)}>Discover</Button>
                  {server.oauth_required && server.oauth_configured && <Button size="sm" variant="outline" icon={<ExternalLink size={14} />} isLoading={oauthMutation.isPending} onClick={() => oauthMutation.mutate(server.id)}>Authorize</Button>}
                  <Button size="sm" variant={server.enabled ? 'secondary' : 'primary'} icon={server.enabled ? <Check size={14} /> : undefined} onClick={() => updateMutation.mutate({ id: server.id, enabled: !server.enabled })}>{server.enabled ? 'Enabled' : 'Enable'}</Button>
                  <Button size="sm" variant="danger" icon={<Trash2 size={14} />} onClick={() => disconnectMutation.mutate(server.id)}>Disconnect</Button>
                </div>
              </div>
              <p className="mt-2 truncate text-xs text-gray-500" title={server.endpoint}>{server.endpoint}</p>
              {server.last_error && <p className="mt-2 text-sm text-red-400">{server.last_error}</p>}
              {expanded && <div className="mt-4 space-y-2 border-t border-white/10 pt-4">
                {!server.oauth_configured && <BearerTokenControls server={server} onSaved={refresh} />}
                {server.tools.length === 0 && <p className="text-sm text-gray-500">No tools discovered yet.</p>}
                {server.tools.map((tool) => <label key={tool.id} className="flex items-start justify-between gap-3 rounded-lg bg-black/10 p-3"><span><span className="block text-sm font-medium">{tool.upstream_name}</span><span className="block text-xs text-gray-500">{tool.namespaced_id} · {tool.description || 'No description supplied'}</span><span className="mt-1 block text-xs text-gray-400" data-testid={`mcp-tool-policy-${tool.id}`}>{tool.read_only ? 'Read-only: runs without asking in chat' : 'Asks before each call in chat'}</span>{tool.injection_suspected && <span className="mt-1 block text-xs text-amber-400" data-testid={`mcp-tool-flag-${tool.id}`}>{tool.injection_acknowledged ? 'Its description looked like a prompt injection; you switched it on anyway.' : 'Switched off: its description looked like a prompt injection. Switch it on only if you trust this server.'}</span>}</span><input type="checkbox" checked={tool.enabled} onChange={(event) => toolMutation.mutate({ serverId: server.id, toolId: tool.id, enabled: event.target.checked })} /></label>)}
              </div>}
            </div>;
          })}
          {(discoverMutation.isError || updateMutation.isError || disconnectMutation.isError || oauthMutation.isError) && <p className="text-sm text-red-400">{errorMessage(discoverMutation.error || updateMutation.error || disconnectMutation.error || oauthMutation.error)}</p>}
        </CardContent>
      </Card>
      <A2AAgentManager />
    </div>
  );
};
