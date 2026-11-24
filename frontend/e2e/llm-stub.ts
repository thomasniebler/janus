import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { api, type Session } from './helpers';

/**
 * A scripted, OpenAI-compatible chat-completions server for e2e specs (JAN-220).
 *
 * The e2e backend has no real LLM. Pointing its OpenAI provider's base URL at this
 * server lets a spec drive the real `/ws/chat` and `/ws/briefing` paths end to end
 * (runtime adapter, tool selection, LangChain agent, `llm_service.py`) while the model
 * output is fixed: the only thing replaced is the provider's HTTP API. Nothing leaves
 * the machine.
 *
 * Every request is answered by the spec's `reply` handler, which picks the text from
 * the request (the last user message, the system prompt, ...). A reply may hold back
 * the rest of its stream on a `gate` so a spec can look at a half-streamed answer.
 */

export interface StubRequest {
  stream: boolean;
  /** Every message's text content, in order. */
  messages: { role: string; content: string }[];
  /** Names of the tools the backend offered the model. */
  toolNames: string[];
}

export interface StubReply {
  /** Streamed in this order; joined for a non-streaming request. */
  chunks: string[];
  /** When set, only the first chunk is sent until this resolves. */
  gate?: Promise<void>;
  /** Answer with this HTTP error status instead (a provider outage). */
  failStatus?: number;
  /** Ask for these tool calls instead of, or before, the text (JAN-343: the model calls a tool). */
  toolCalls?: { name: string; arguments: Record<string, unknown> }[];
}

export type StubHandler = (request: StubRequest) => StubReply;

const messageText = (content: unknown): string => {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.map((part) => (typeof part?.text === 'string' ? part.text : '')).join('');
  }
  return '';
};

/** The text of the last user message in a request. */
export const lastUserMessage = (request: StubRequest): string =>
  [...request.messages].reverse().find((message) => message.role === 'user')?.content ?? '';

/** True for the Morning briefing trigger run's request (its objective starts the user message). */
export const isBriefingRequest = (request: StubRequest): boolean =>
  lastUserMessage(request).includes('Write my morning briefing.');

/** First line of the backend's tool-selection request (JAN-300, `toolset_selection.SELECTOR_PROMPT_MARKER`). */
export const TOOLSET_SELECTOR_MARKER = 'You choose which Janus toolboxes a task needs.';

/** A tool selection the stub answers with (JAN-300). */
export interface StubToolsetSelection {
  tool_ids: string[];
  reason: string;
}

/**
 * Answers a helper request (conversation title, memory suggestion, tool selection) without storing
 * anything. Without a scripted selection the tool-selection answer is empty, so the backend falls back
 * to every authorized tool and specs see the same tools as before JAN-300.
 */
const helperReply = (request: StubRequest, selection: StubToolsetSelection | null): StubReply | null => {
  const prompt = request.messages.map((message) => message.content).join('\n');
  if (prompt.includes(TOOLSET_SELECTOR_MARKER)) {
    return { chunks: [JSON.stringify(selection ?? { tool_ids: [], reason: 'e2e stub: no selection scripted' })] };
  }
  if (prompt.includes('durable fact Janus should remember')) {
    return { chunks: ['{"store": false, "summary": "", "detail": null}'] };
  }
  if (prompt.includes('Generate a very short title')) {
    return { chunks: ['E2E stub conversation'] };
  }
  return null;
};

const chunkFrame = (delta: Record<string, unknown>, finishReason: string | null) =>
  `data: ${JSON.stringify({
    id: 'chatcmpl-e2e-stub',
    object: 'chat.completion.chunk',
    created: 0,
    model: 'e2e-stub',
    choices: [{ index: 0, delta, finish_reason: finishReason }],
  })}\n\n`;

export class LLMStub {
  readonly requests: StubRequest[] = [];
  private server: http.Server | null = null;
  private handler: StubHandler = () => ({ chunks: ['(no reply scripted)'] });

  private toolsetSelection: StubToolsetSelection | null = null;

  /** Sets how the next requests are answered. */
  reply(handler: StubHandler) {
    this.handler = handler;
  }

  /** Answers the backend's tool-selection requests with this selection (JAN-300); `null` restores the fallback. */
  selectTools(selection: StubToolsetSelection | null) {
    this.toolsetSelection = selection;
  }

  get baseUrl(): string {
    const address = this.server?.address() as AddressInfo | null;
    if (!address) throw new Error('LLM stub is not running');
    return `http://127.0.0.1:${address.port}/v1`;
  }

  async start(): Promise<void> {
    this.server = http.createServer((req, res) => {
      void this.handle(req, res);
    });
    await new Promise<void>((resolve) => this.server!.listen(0, '127.0.0.1', resolve));
  }

  async stop(): Promise<void> {
    const server = this.server;
    this.server = null;
    if (!server) return;
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }

  /** Points the backend's default model (chat and utility) at this stub. */
  async useAsBackendModel(admin: Session) {
    await api(admin, 'patch', '/api/planner/settings', {
      llm_provider: 'openai',
      openai_model: 'e2e-stub',
      openai_api_key: 'e2e-stub-key',
      openai_base_url: this.baseUrl,
    });
  }

  /** Restores the e2e default: Ollama (unreachable), no OpenAI key or URL. */
  static async resetBackendModel(admin: Session) {
    await api(admin, 'patch', '/api/planner/settings', {
      llm_provider: 'ollama',
      openai_api_key: '',
      openai_base_url: '',
    });
  }

  private async handle(req: http.IncomingMessage, res: http.ServerResponse) {
    if (req.method !== 'POST' || !req.url?.endsWith('/chat/completions')) {
      res.writeHead(404).end();
      return;
    }
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    const request: StubRequest = {
      stream: Boolean(body.stream),
      messages: (body.messages ?? []).map((message: { role: string; content: unknown }) => ({
        role: message.role,
        content: messageText(message.content),
      })),
      toolNames: (body.tools ?? []).map((tool: { function?: { name?: string } }) => tool.function?.name ?? ''),
    };
    const reply = helperReply(request, this.toolsetSelection) ?? this.handler(request);
    this.requests.push(request);

    if (reply.failStatus) {
      res.writeHead(reply.failStatus, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: { message: 'e2e stub outage', type: 'server_error' } }));
      return;
    }
    const toolCalls = (reply.toolCalls ?? []).map((call, index) => ({
      index,
      id: `call_e2e_${index}`,
      type: 'function',
      function: { name: call.name, arguments: JSON.stringify(call.arguments) },
    }));
    if (!request.stream) {
      if (reply.gate) await reply.gate;
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(
        JSON.stringify({
          id: 'chatcmpl-e2e-stub',
          object: 'chat.completion',
          created: 0,
          model: 'e2e-stub',
          choices: [
            {
              index: 0,
              message: {
                role: 'assistant',
                content: reply.chunks.join(''),
                ...(toolCalls.length ? { tool_calls: toolCalls } : {}),
              },
              finish_reason: toolCalls.length ? 'tool_calls' : 'stop',
            },
          ],
          usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
        }),
      );
      return;
    }

    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
    if (toolCalls.length) {
      res.write(chunkFrame({ role: 'assistant', content: null, tool_calls: toolCalls }, null));
      res.write(chunkFrame({}, 'tool_calls'));
      res.end('data: [DONE]\n\n');
      return;
    }
    const [first, ...rest] = reply.chunks;
    res.write(chunkFrame({ role: 'assistant', content: first ?? '' }, null));
    if (reply.gate) await reply.gate;
    for (const chunk of rest) res.write(chunkFrame({ content: chunk }, null));
    res.write(chunkFrame({}, 'stop'));
    res.end('data: [DONE]\n\n');
  }
}
