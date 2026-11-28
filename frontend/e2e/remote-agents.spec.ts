import { type Page } from '@playwright/test';
import { startFakeUpstream, type FakeUpstream, type RecordedRequest } from './fake-upstream';
import { expect, test } from './fixtures';
import { api, trackChatSocketOpen, waitForOpenChatSocket } from './helpers';
import { isBriefingRequest, LLMStub, lastUserMessage, type StubRequest } from './llm-stub';

// JAN-404: remote A2A agents. A local fake agent serves an A2A 1.x agent card and answers
// SendMessage over JSON-RPC (loopback HTTP is allowed for local agents). The settings test
// drives the whole registry lifecycle through the UI; the chat test registers the agent over
// the API and runs a real chat turn in which the LLM stub asks the agent, the user approves
// the exact prompt, and the backend sends it. Every test runs as its own throwaway user.

const TOKEN = 'e2e-a2a-token';
const AGENT_NAME = 'E2E research agent';

test.use({ asThrowawayUser: true });

const startFakeAgent = async (): Promise<FakeUpstream> => {
  // The handler runs only after the server is up, so it may read `fake.url`.
  const fake: FakeUpstream = await startFakeUpstream(0, (request: RecordedRequest) => {
    if (request.method === 'GET' && request.url === '/.well-known/agent-card.json') {
      return {
        json: {
          name: 'Fake Research Agent',
          description: 'Answers research questions for the e2e suite.',
          version: '1.0.0',
          supportedInterfaces: [{ url: `${fake.url}/a2a`, protocolBinding: 'JSONRPC', protocolVersion: '1.0' }],
          capabilities: {},
          defaultInputModes: ['text/plain'],
          defaultOutputModes: ['text/plain'],
          skills: [{ id: 'lit', name: 'Literature search', description: 'Finds papers' }],
        },
      };
    }
    if (request.method === 'POST' && request.url === '/a2a') {
      const body = JSON.parse(request.body) as { id: number; method: string };
      return {
        json: {
          jsonrpc: '2.0',
          id: body.id,
          result: { message: { role: 'ROLE_AGENT', parts: [{ text: 'Three relevant papers found.' }] } },
        },
      };
    }
    return undefined;
  });
  return fake;
};

/** The JSON-RPC messages the fake agent received, oldest first. */
const sentMessages = (fake: FakeUpstream) =>
  fake.requests
    .filter((request) => request.method === 'POST' && request.url === '/a2a')
    .map((request) => ({
      body: JSON.parse(request.body) as { method: string; params: { message: { parts: { text: string }[] } } },
      authorization: request.headers.authorization,
    }));

test.describe('Remote agents in settings (JAN-404)', () => {
  let fake: FakeUpstream;
  test.beforeAll(async () => {
    fake = await startFakeAgent();
  });
  test.afterAll(async () => {
    await fake.close();
  });

  test('add, switch on, refresh, and remove a remote agent; the token never comes back', async ({ page }) => {
    await page.goto('/settings?tab=tools');
    await page.getByRole('button', { name: 'Add remote agent' }).click();
    await page.getByLabel('Name', { exact: true }).fill(AGENT_NAME);
    await page.getByLabel('Agent URL').fill(fake.url);
    await page.getByLabel('Bearer token (optional)').fill(TOKEN);
    await page.getByRole('button', { name: 'Add and fetch card' }).click();

    const row = page.getByTestId(/^a2a-agent-/);
    await expect(row).toContainText(AGENT_NAME);
    await expect(row).toContainText('Card: Fake Research Agent · A2A 1.0 · bearer token saved');
    await row.getByText('Skills the agent advertises').click();
    await expect(row.getByText('Literature search')).toBeVisible();

    const listed = await (await page.request.get('/api/tools/a2a-agents')).text();
    expect(listed).not.toContain(TOKEN);

    await row.getByRole('button', { name: 'Switch on' }).click();
    await expect(row.getByRole('button', { name: 'Switch off' })).toBeVisible();

    const before = fake.requests.length;
    await row.getByRole('button', { name: 'Refresh card' }).click();
    await expect.poll(() => fake.requests.length).toBeGreaterThan(before);
    await expect(row.getByRole('button', { name: 'Switch off' })).toBeVisible();

    await row.getByRole('button', { name: `Remove ${AGENT_NAME}` }).click();
    await expect(page.getByTestId(/^a2a-agent-/)).toHaveCount(0);
    await expect(page.getByText('No remote agents yet.')).toBeVisible();
  });

  test('refuses an agent URL on a private network', async ({ page }) => {
    await page.goto('/settings?tab=tools');
    await page.getByRole('button', { name: 'Add remote agent' }).click();
    await page.getByLabel('Name', { exact: true }).fill('Private');
    await page.getByLabel('Agent URL').fill('http://10.0.0.1');
    await page.getByRole('button', { name: 'Add and fetch card' }).click();
    await expect(page.getByText('The agent URL resolves to a blocked network address.')).toBeVisible();
  });
});

test.describe('Remote agents in chat (JAN-404)', () => {
  let fake: FakeUpstream;
  const stub = new LLMStub();

  test.beforeAll(async () => {
    fake = await startFakeAgent();
    await stub.start();
  });
  test.afterAll(async () => {
    await stub.stop();
    await fake.close();
  });
  test.beforeEach(async ({ adminRequest }) => {
    await stub.useAsBackendModel(adminRequest);
  });
  test.afterEach(async ({ adminRequest }) => {
    await LLMStub.resetBackendModel(adminRequest);
  });

  const registerAgent = async (page: Page) => {
    await page.goto('/');
    const created = (await (
      await api(page, 'post', '/api/tools/a2a-agents', { name: AGENT_NAME, base_url: fake.url, token: TOKEN })
    ).json()) as { id: number };
    await api(page, 'patch', `/api/tools/a2a-agents/${created.id}`, { enabled: true });
    return `ask_agent_${created.id}`;
  };

  test('approving sends exactly the shown prompt with the token, and the answer is used', async ({ page }) => {
    const tool = await registerAgent(page);
    stub.reply((request: StubRequest) => {
      if (isBriefingRequest(request)) return { chunks: ['Stub briefing.'] };
      const messages = request.messages;
      const userIndex = messages.map((message) => message.role).lastIndexOf('user');
      const toolResult = messages.slice(userIndex + 1).find((message) => message.role === 'tool');
      if (toolResult) {
        return { chunks: [toolResult.content.includes('Three relevant papers') ? 'The agent found three papers.' : 'No answer.'] };
      }
      if (lastUserMessage(request).includes('Ask the research agent')) {
        return { chunks: [], toolCalls: [{ name: tool, arguments: { prompt: 'Find papers on A2A security.' } }] };
      }
      return { chunks: ['(unexpected)'] };
    });

    await trackChatSocketOpen(page);
    await page.goto('/');
    const chat = page.getByRole('complementary', { name: 'Chat with Janus' });
    const input = chat.getByPlaceholder('Ask a question...');
    await expect(input).toBeVisible();
    await waitForOpenChatSocket(page);
    const before = sentMessages(fake).length;

    await input.fill('Ask the research agent about A2A security papers');
    await input.press('Enter');

    const card = chat.getByRole('group', { name: `Janus wants to use ${AGENT_NAME} (remote agent)` });
    await expect(card).toBeVisible();
    await expect(card.getByText(/Find papers on A2A security\./)).toBeVisible();
    expect(sentMessages(fake).slice(before)).toEqual([]);

    await card.getByRole('button', { name: 'Approve' }).click();
    await expect(chat.getByText('The agent found three papers.')).toBeVisible();
    const sent = sentMessages(fake).slice(before);
    expect(sent).toHaveLength(1);
    expect(sent[0].body.method).toBe('SendMessage');
    expect(sent[0].body.params.message.parts).toEqual([{ text: 'Find papers on A2A security.' }]);
    expect(sent[0].authorization).toBe(`Bearer ${TOKEN}`);
  });
});
