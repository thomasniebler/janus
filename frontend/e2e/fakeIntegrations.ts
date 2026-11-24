import { createHash, randomBytes } from 'node:crypto';
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';

/** One request the fake services received, as the e2e backend sent it. */
export interface RecordedRequest {
  method: string;
  path: string;
  /** The query string's parameters. */
  query: URLSearchParams;
  headers: IncomingMessage['headers'];
  body: string;
}

/** What the fake OAuth provider checked when Janus redeemed an authorization code. */
export interface TokenExchange {
  pkceVerified: boolean;
  redirectUri: string;
}

export const FAKE_MCP_ACCESS_TOKEN = 'e2e-mcp-access-token';
export const FAKE_MCP_CLIENT_ID = 'janus-e2e-client';
/** A model id the fake LLM providers refuse with 404, for the failed connection test. */
export const FAKE_UNKNOWN_MODEL = 'e2e-unknown-model';
/** An Azure OpenAI key the fake accepts for chat but not for listing deployments (403 PermissionDenied). */
export const FAKE_AZURE_DENIED_KEY = 'azure-e2e-denied';

/**
 * A loopback stand-in for every outbound service the Settings integrations talk to
 * (JAN-222), so the real e2e backend makes real HTTP calls and nothing reaches the internet:
 *
 * - LLM providers: Ollama (`/api/tags`, `/api/chat`), OpenAI-compatible (`/v1/models`,
 *   `/v1/chat/completions`; OpenRouter under `/openrouter`), Anthropic (`/v1/models`
 *   with `x-api-key`, `/v1/messages`), and GitHub Models (`/github/catalog/models`,
 *   `/github/inference/chat/completions`; JAN-340), and Azure OpenAI at the server root
 *   (`/openai/deployments`, denied for `FAKE_AZURE_DENIED_KEY`, and
 *   `/openai/deployments/<deployment>/chat/completions`; JAN-349, and the v1 endpoint's
 *   `/openai/v1/chat/completions`; JAN-356).
 * - A streamable-HTTP MCP server (`/mcp`) that answers 401 without the OAuth access token.
 * - That server's OAuth provider (`/oauth/authorize`, `/oauth/token`), which checks the
 *   PKCE verifier against the challenge before it issues a token.
 *
 * Every request is recorded, so specs can check which credentials went where.
 */
export class FakeIntegrations {
  readonly requests: RecordedRequest[] = [];
  readonly tokenExchanges: TokenExchange[] = [];
  /** When set, the next authorization request is denied (`error=access_denied`). */
  denyNextAuthorization = false;

  private server: Server | null = null;
  private readonly pendingCodes = new Map<string, { challenge: string; redirectUri: string; clientId: string }>();

  get url(): string {
    const { port } = this.server!.address() as AddressInfo;
    return `http://127.0.0.1:${port}`;
  }

  async start(): Promise<void> {
    this.server = createServer((request, response) => {
      const chunks: Buffer[] = [];
      request.on('data', (chunk: Buffer) => chunks.push(chunk));
      request.on('end', () => {
        const body = Buffer.concat(chunks).toString('utf-8');
        const url = new URL(request.url ?? '/', 'http://fake');
        this.requests.push({
          method: request.method ?? 'GET',
          path: url.pathname,
          query: url.searchParams,
          headers: request.headers,
          body,
        });
        this.route(request, url, body, response);
      });
    });
    await new Promise<void>((resolve) => this.server!.listen(0, '127.0.0.1', resolve));
  }

  async stop(): Promise<void> {
    await new Promise<void>((resolve) => (this.server ? this.server.close(() => resolve()) : resolve()));
    this.server = null;
  }

  /** The recorded requests to one path. */
  requestsTo(path: string): RecordedRequest[] {
    return this.requests.filter((request) => request.path === path);
  }

  private route(request: IncomingMessage, url: URL, body: string, response: ServerResponse) {
    const { pathname } = url;
    const json = body && request.headers['content-type']?.includes('json') ? JSON.parse(body) : {};
    if (request.method === 'GET' && pathname === '/api/tags') {
      return send(response, 200, { models: [{ name: 'llama-e2e:latest' }, { name: 'qwen-e2e:7b' }] });
    }
    if (request.method === 'POST' && pathname === '/api/chat') {
      if (json.model === FAKE_UNKNOWN_MODEL) return send(response, 404, { error: `model '${json.model}' not found` });
      return send(response, 200, {
        model: json.model,
        created_at: '2026-01-01T00:00:00Z',
        message: { role: 'assistant', content: 'pong' },
        done: true,
        done_reason: 'stop',
      });
    }
    if (request.method === 'GET' && pathname === '/v1/models') {
      if (request.headers['x-api-key']) {
        return send(response, 200, {
          data: [
            { id: 'claude-e2e-opus', display_name: 'Claude E2E Opus' },
            { id: 'claude-e2e-haiku', display_name: 'Claude E2E Haiku' },
          ],
          has_more: false,
        });
      }
      // Embedding models are listed too, as OpenAI does; Janus leaves them out of the chat list.
      return send(response, 200, { data: [{ id: 'gpt-e2e-mini' }, { id: 'gpt-e2e-large' }, { id: 'text-embedding-e2e' }] });
    }
    if (request.method === 'GET' && pathname === '/openrouter/models') {
      return send(response, 200, { data: [{ id: 'e2e/router-model', name: 'E2E Router Model' }] });
    }
    if (request.method === 'GET' && pathname === '/github/catalog/models') {
      // The real catalog lists embedding models too; Janus keeps only text-output models.
      return send(response, 200, [
        { id: 'openai/gpt-e2e', name: 'OpenAI GPT E2E', supported_output_modalities: ['text'] },
        { id: 'meta/llama-e2e', name: 'Meta Llama E2E', supported_output_modalities: ['text'] },
        { id: 'openai/text-embedding-e2e', name: 'Embedding E2E', supported_output_modalities: ['embeddings'] },
      ]);
    }
    if (request.method === 'GET' && pathname === '/openai/deployments') {
      if (request.headers['api-key'] === FAKE_AZURE_DENIED_KEY) {
        return send(response, 403, { error: { code: 'PermissionDenied', message: 'The principal lacks the required data action.' } });
      }
      return send(response, 200, {
        data: [
          { id: 'Kimi-K2.6', model: 'Kimi-K2.6', status: 'succeeded' },
          { id: 'chat-e2e', model: 'gpt-4.1-mini', status: 'succeeded' },
        ],
      });
    }
    const chatPaths = [
      '/v1/chat/completions',
      '/openrouter/chat/completions',
      '/github/inference/chat/completions',
      '/openai/v1/chat/completions',
    ];
    const azureChat = /^\/openai\/deployments\/[^/]+\/chat\/completions$/.test(pathname);
    if (request.method === 'POST' && (chatPaths.includes(pathname) || azureChat)) {
      if (json.model === FAKE_UNKNOWN_MODEL) {
        return send(response, 404, { error: { message: `The model ${json.model} does not exist`, type: 'invalid_request_error' } });
      }
      return send(response, 200, {
        id: 'chatcmpl-e2e',
        object: 'chat.completion',
        created: 1_767_225_600,
        model: json.model,
        choices: [{ index: 0, message: { role: 'assistant', content: 'pong' }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
      });
    }
    if (request.method === 'POST' && pathname === '/v1/messages') {
      return send(response, 200, {
        id: 'msg_e2e',
        type: 'message',
        role: 'assistant',
        model: json.model,
        content: [{ type: 'text', text: 'pong' }],
        stop_reason: 'end_turn',
        stop_sequence: null,
        usage: { input_tokens: 1, output_tokens: 1 },
      });
    }
    if (pathname === '/mcp') return this.mcp(request, json, response);
    if (request.method === 'GET' && pathname === '/oauth/authorize') return this.authorize(url, response);
    if (request.method === 'POST' && pathname === '/oauth/token') return this.token(body, response);
    return send(response, 404, { error: `no fake route for ${request.method} ${pathname}` });
  }

  private mcp(
    request: IncomingMessage,
    json: { id?: number; method?: string; params?: { name?: string } },
    response: ServerResponse,
  ) {
    if (request.headers.authorization !== `Bearer ${FAKE_MCP_ACCESS_TOKEN}`) {
      return send(response, 401, { error: 'unauthorized' });
    }
    if (json.method === 'initialize') {
      response.setHeader('mcp-session-id', 'e2e-session');
      return send(response, 200, {
        jsonrpc: '2.0',
        id: json.id,
        result: { protocolVersion: '2025-03-26', capabilities: { tools: {} }, serverInfo: { name: 'e2e-mcp', version: '1.0' } },
      });
    }
    if (json.method === 'notifications/initialized') {
      response.writeHead(202);
      return response.end();
    }
    if (json.method === 'tools/list') {
      return send(response, 200, {
        jsonrpc: '2.0',
        id: json.id,
        result: {
          tools: [
            { name: 'e2e_lookup', description: 'Looks up an E2E record.', inputSchema: { type: 'object' } },
            { name: 'e2e_status', description: 'Reports the E2E status.', inputSchema: { type: 'object' } },
            // JAN-343: a read-only record lookup (runs without asking in chat) and a write (asks first).
            {
              name: 'e2e_fetch_record',
              description: 'Fetches one E2E record by id.',
              inputSchema: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] },
              annotations: { readOnlyHint: true },
            },
            {
              name: 'e2e_update_record',
              description: 'Updates one E2E record.',
              inputSchema: {
                type: 'object',
                properties: {
                  id: { type: 'string' },
                  due_date: { anyOf: [{ type: 'string' }, { type: 'null' }] },
                },
                required: ['id'],
              },
            },
          ],
        },
      });
    }
    if (json.method === 'tools/call') {
      const text = json.params?.name === 'e2e_fetch_record' ? 'E2E record 42: status paid.' : 'E2E record updated.';
      return send(response, 200, { jsonrpc: '2.0', id: json.id, result: { content: [{ type: 'text', text }] } });
    }
    return send(response, 200, { jsonrpc: '2.0', id: json.id, error: { code: -32601, message: 'Method not found' } });
  }

  private authorize(url: URL, response: ServerResponse) {
    const params = url.searchParams;
    const redirectUri = params.get('redirect_uri') ?? '';
    const state = params.get('state') ?? '';
    const target = new URL(redirectUri);
    target.searchParams.set('state', state);
    const valid =
      params.get('response_type') === 'code' &&
      params.get('client_id') === FAKE_MCP_CLIENT_ID &&
      params.get('code_challenge_method') === 'S256' &&
      Boolean(params.get('code_challenge'));
    if (this.denyNextAuthorization || !valid) {
      this.denyNextAuthorization = false;
      target.searchParams.set('error', 'access_denied');
    } else {
      const code = randomBytes(16).toString('hex');
      this.pendingCodes.set(code, {
        challenge: params.get('code_challenge')!,
        redirectUri,
        clientId: params.get('client_id')!,
      });
      target.searchParams.set('code', code);
    }
    response.writeHead(302, { location: target.toString() });
    response.end();
  }

  private token(body: string, response: ServerResponse) {
    const form = new URLSearchParams(body);
    const pending = this.pendingCodes.get(form.get('code') ?? '');
    this.pendingCodes.delete(form.get('code') ?? '');
    const verifier = form.get('code_verifier') ?? '';
    const pkceVerified =
      pending !== undefined && createHash('sha256').update(verifier).digest('base64url') === pending.challenge;
    this.tokenExchanges.push({ pkceVerified, redirectUri: form.get('redirect_uri') ?? '' });
    if (
      form.get('grant_type') !== 'authorization_code' ||
      !pending ||
      !pkceVerified ||
      form.get('redirect_uri') !== pending.redirectUri ||
      form.get('client_id') !== pending.clientId
    ) {
      return send(response, 400, { error: 'invalid_grant' });
    }
    return send(response, 200, {
      access_token: FAKE_MCP_ACCESS_TOKEN,
      token_type: 'Bearer',
      expires_in: 3600,
      refresh_token: 'e2e-mcp-refresh-token',
      scope: 'tools.read',
    });
  }
}

const send = (response: ServerResponse, status: number, payload: unknown) => {
  response.writeHead(status, { 'content-type': 'application/json' });
  response.end(JSON.stringify(payload));
};
