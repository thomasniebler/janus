import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosResponse } from 'axios';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { mcpApi } from '../../api';
import type { MCPRegistry, MCPServer } from '../../types';
import { MCPRegistryManager } from './MCPRegistryManager';

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});
// Same factory shape as InjectionClassifierCard.test: with `isolate: false` the first registration wins
// for the worker, so a different shape here would leak into that file.
vi.mock('../../lib/injectionClassifier', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/injectionClassifier')>()),
  injectionClassifierApi: { getStatus: vi.fn(), update: vi.fn() },
}));

const response = <T,>(data: T): AxiosResponse<T> => ({ data }) as AxiosResponse<T>;

const server = (overrides: Partial<MCPServer>): MCPServer => ({
  id: 3,
  provider_name: 'Local MCP',
  endpoint: 'http://127.0.0.1:8787/mcp',
  transport: 'streamable_http',
  enabled: true,
  discovery_status: 'never',
  last_error: null,
  oauth_required: false,
  oauth_configured: false,
  credential_configured: false,
  credential_status: 'none',
  tools: [],
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  ...overrides,
});

const registry = (servers: MCPServer[]): MCPRegistry => ({ builtins: [], remote_servers: servers });

const renderManager = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MCPRegistryManager />
    </QueryClientProvider>
  );
};

describe('MCPRegistryManager bearer tokens', () => {
  it('sends a bearer token when adding a server', async () => {
    vi.mocked(mcpApi.getRegistry).mockResolvedValue(response(registry([])));
    vi.mocked(mcpApi.createServer).mockResolvedValue(response(server({ credential_status: 'static' })));
    renderManager();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /add mcp server/i }));
    await user.type(screen.getByLabelText(/provider name/i), 'Local MCP');
    await user.type(screen.getByLabelText(/http mcp endpoint/i), 'http://127.0.0.1:8787/mcp');
    const token = screen.getByLabelText(/bearer token/i);
    expect(token).toHaveAttribute('type', 'password');
    await user.type(token, ' tok-123 ');
    await user.click(screen.getByRole('button', { name: /save server/i }));

    expect(mcpApi.createServer).toHaveBeenCalledWith(
      expect.objectContaining({ provider_name: 'Local MCP', access_token: 'tok-123' })
    );
  });

  it('shows a rejected token and lets the owner replace or remove it', async () => {
    vi.mocked(mcpApi.getRegistry).mockResolvedValue(
      response(registry([server({ discovery_status: 'auth_required', credential_configured: true, credential_status: 'static' })]))
    );
    vi.mocked(mcpApi.updateServer).mockResolvedValue(response(server({ credential_status: 'static' })));
    renderManager();
    const user = userEvent.setup();

    expect(await screen.findByTestId('mcp-credential-3')).toHaveTextContent('Bearer token');
    expect(screen.getByText('Token rejected')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /authorize/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /local mcp/i }));
    await user.type(screen.getByLabelText(/replace bearer token/i), 'tok-new');
    await user.click(screen.getByRole('button', { name: /save token/i }));
    expect(mcpApi.updateServer).toHaveBeenCalledWith(3, { access_token: 'tok-new' });

    await user.click(screen.getByRole('button', { name: /remove token/i }));
    expect(mcpApi.updateServer).toHaveBeenCalledWith(3, { clear_access_token: true });
  });

  it('shows servers set up for OAuth without token controls', async () => {
    vi.mocked(mcpApi.getRegistry).mockResolvedValue(
      response(registry([server({ oauth_configured: true, credential_configured: true, credential_status: 'oauth' })]))
    );
    renderManager();
    const user = userEvent.setup();

    expect(await screen.findByTestId('mcp-credential-3')).toHaveTextContent('OAuth');
    await user.click(screen.getByRole('button', { name: /local mcp/i }));
    expect(screen.queryByLabelText(/bearer token/i)).not.toBeInTheDocument();
  });

  it('lets an OAuth credential without OAuth settings be replaced by a token', async () => {
    vi.mocked(mcpApi.getRegistry).mockResolvedValue(
      response(registry([server({ oauth_configured: false, credential_configured: true, credential_status: 'oauth' })]))
    );
    vi.mocked(mcpApi.updateServer).mockResolvedValue(response(server({ credential_status: 'static' })));
    renderManager();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /local mcp/i }));
    expect(screen.getByText(/replaces this server's oauth connection/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /remove token/i })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText(/^bearer token/i), 'tok-x');
    await user.click(screen.getByRole('button', { name: /save token/i }));
    expect(mcpApi.updateServer).toHaveBeenCalledWith(3, { access_token: 'tok-x' });
  });

  it('shows how chat treats each discovered tool', async () => {
    const tool = (id: number, readOnly: boolean) => ({
      id,
      upstream_name: readOnly ? 'search' : 'send',
      namespaced_id: `mcp.3.${readOnly ? 'search' : 'send'}`,
      description: '',
      input_schema: {},
      enabled: true,
      read_only: readOnly,
      confirmation: readOnly ? ('never' as const) : ('required' as const),
      created_at: '2026-07-01T00:00:00Z',
      updated_at: '2026-07-01T00:00:00Z',
    });
    vi.mocked(mcpApi.getRegistry).mockResolvedValue(
      response(registry([server({ discovery_status: 'ready', tools: [tool(1, true), tool(2, false)] })]))
    );
    renderManager();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /local mcp/i }));
    expect(screen.getByTestId('mcp-tool-policy-1')).toHaveTextContent('Read-only: runs without asking in chat');
    expect(screen.getByTestId('mcp-tool-policy-2')).toHaveTextContent('Asks before each call in chat');
  });

  it('warns about a tool switched off for a suspicious description', async () => {
    const flagged = {
      id: 7,
      upstream_name: 'bad',
      namespaced_id: 'mcp.3.bad',
      description: '',
      input_schema: {},
      enabled: false,
      read_only: false,
      confirmation: 'required' as const,
      injection_suspected: true,
      injection_acknowledged: false,
      created_at: '2026-07-01T00:00:00Z',
      updated_at: '2026-07-01T00:00:00Z',
    };
    vi.mocked(mcpApi.getRegistry).mockResolvedValue(
      response(registry([server({ discovery_status: 'ready', tools: [flagged] })]))
    );
    renderManager();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /local mcp/i }));
    expect(screen.getByTestId('mcp-tool-flag-7')).toHaveTextContent(/looked like a prompt injection/i);
  });
});

