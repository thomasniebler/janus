import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { mcpApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { MCPRegistryManager } from './MCPRegistryManager';
import { toolRegistrySample } from './triggers/triggerFixtures';
import type { MCPRegistry } from '../../types';

const registry: MCPRegistry = {
  // Real capability ids; "Send mail" is allowed unattended by the user, so it shows as changed.
  builtins: toolRegistrySample.builtins.map((tool) =>
    tool.id === 'mail.send' ? { ...tool, unattended_allowed: true } : tool
  ),
  remote_servers: [
    {
      id: 1,
      provider_name: 'Notion',
      endpoint: 'https://mcp.notion.example.com/mcp',
      transport: 'streamable_http',
      enabled: true,
      discovery_status: 'ready',
      last_error: null,
      oauth_required: true,
      oauth_configured: true,
      credential_configured: true,
      credential_status: 'oauth',
      tools: [
        {
          id: 11,
          upstream_name: 'search_pages',
          namespaced_id: 'notion.search_pages',
          description: 'Full-text search across the connected workspace.',
          input_schema: {},
          enabled: true,
          read_only: true,
          confirmation: 'never',
          created_at: '2026-08-01T00:00:00Z',
          updated_at: '2026-08-01T00:00:00Z',
        },
      ],
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-09-20T00:00:00Z',
    },
    {
      id: 2,
      provider_name: 'Broken MCP',
      endpoint: 'https://mcp.broken.example.com/mcp',
      transport: 'streamable_http',
      enabled: false,
      discovery_status: 'error',
      last_error: 'Discovery failed: connection timed out after 10s.',
      oauth_required: false,
      oauth_configured: false,
      credential_configured: false,
      credential_status: 'none',
      tools: [],
      created_at: '2026-07-01T00:00:00Z',
      updated_at: '2026-07-01T00:00:00Z',
    },
    {
      id: 3,
      provider_name: 'Local MCP',
      endpoint: 'http://127.0.0.1:8787/mcp',
      transport: 'streamable_http',
      enabled: true,
      discovery_status: 'auth_required',
      last_error: 'Remote MCP server rejected the bearer token. Save a new token for this server.',
      oauth_required: false,
      oauth_configured: false,
      credential_configured: true,
      credential_status: 'static',
      tools: [],
      created_at: '2026-07-01T00:00:00Z',
      updated_at: '2026-07-01T00:00:00Z',
    },
  ],
};

const seed =
  (data: MCPRegistry) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['mcp-registry'], data);
    queryClient.setQueryData(['injection-classifier'], {
      enabled: false,
      model: 'ollama/llama3:latest (default)',
      is_cloud: false,
      monthly_token_budget: 200000,
      period: '2026-10',
      tokens_used: 0,
      calls: 0,
      cached_verdicts: 0,
    });
  };

const meta = {
  title: 'Features/MCPRegistryManager',
  component: MCPRegistryManager,
} satisfies Meta<typeof MCPRegistryManager>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Populated: Story = {
  decorators: [withQueryData(seed(registry))],
};

export const NoRemoteServers: Story = {
  decorators: [withQueryData(seed({ ...registry, remote_servers: [] }))],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(mcpApi, 'getRegistry', () => neverResolving())],
};

export const RegistryUnavailable: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(mcpApi, 'getRegistry', () => Promise.reject(new Error('registry unavailable'))),
  ],
};
