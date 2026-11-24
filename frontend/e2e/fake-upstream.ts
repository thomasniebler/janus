import http from 'node:http';
import { E2E_HOST } from './env';

/** One request the backend sent to the fake, with its body already read. */
export interface RecordedRequest {
  method: string;
  /** Path and query, e.g. `/google/token` or `/jira/rest/api/3/search/jql?jql=...`. */
  url: string;
  headers: http.IncomingHttpHeaders;
  body: string;
}

export interface FakeResponse {
  status?: number;
  json?: unknown;
}

/** Answers one request, or returns `undefined` for a 404. */
export type FakeHandler = (request: RecordedRequest) => FakeResponse | undefined;

export interface FakeUpstream {
  /** Base URL the fake listens on, e.g. `http://127.0.0.1:9139`. */
  url: string;
  /** Every request received so far, oldest first. */
  requests: RecordedRequest[];
  /** Replaces the handler, e.g. to make the next token exchange fail. */
  setHandler: (handler: FakeHandler) => void;
  close: () => Promise<void>;
}

/**
 * Starts a local HTTP server that stands in for an external API the backend calls
 * (Google's token, userinfo, Calendar and Tasks endpoints, a Jira site; JAN-221), so
 * no spec ever reaches the real service. Every request is recorded for assertions.
 * `port` 0 picks a free port (fine whenever the spec hands the backend `url` at run
 * time, such as an LLM provider's base URL); a URL the backend reads at start needs
 * the fixed E2E_FAKE_UPSTREAM_PORT from `env.ts`.
 */
export const startFakeUpstream = async (port: number, handler: FakeHandler): Promise<FakeUpstream> => {
  const requests: RecordedRequest[] = [];
  let current = handler;
  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => chunks.push(chunk));
    req.on('end', () => {
      const recorded: RecordedRequest = {
        method: req.method ?? 'GET',
        url: req.url ?? '/',
        headers: req.headers,
        body: Buffer.concat(chunks).toString('utf8'),
      };
      requests.push(recorded);
      let answer: FakeResponse | undefined;
      try {
        answer = current(recorded);
      } catch (error) {
        answer = { status: 500, json: { error: String(error) } };
      }
      const status = answer?.status ?? (answer ? 200 : 404);
      res.writeHead(status, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(answer?.json ?? { error: 'not found in the e2e fake', url: recorded.url }));
    });
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, E2E_HOST, () => resolve());
  });
  const address = server.address();
  const boundPort = typeof address === 'object' && address ? address.port : port;
  return {
    url: `http://${E2E_HOST}:${boundPort}`,
    requests,
    setHandler: (next) => {
      current = next;
    },
    close: () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections();
        server.close(() => resolve());
      }),
  };
};
