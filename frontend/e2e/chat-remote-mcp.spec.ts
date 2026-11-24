import { type Page } from '@playwright/test';
import { FAKE_MCP_ACCESS_TOKEN, FakeIntegrations } from './fakeIntegrations';
import { expect, test } from './fixtures';
import { api, trackChatSocketOpen, waitForOpenChatSocket } from './helpers';
import { isBriefingRequest, LLMStub, lastUserMessage, type StubRequest } from './llm-stub';

// JAN-343: a discovered remote MCP tool offered in chat. The e2e backend registers a server
// on the fake MCP endpoint (FakeIntegrations `/mcp`, with the static bearer token of JAN-342),
// discovers its tools, and runs a real chat turn: the LLM stub asks for the tool calls, and
// the backend makes the MCP calls. A read-only tool runs without asking; a write tool shows an
// approval card with its exact arguments. Approve sends exactly those arguments upstream; deny
// sends nothing. Every test runs as its own throwaway user (JAN-336).

const PLACEHOLDER = 'Ask a question...';
const PROVIDER = 'E2E remote';

test.use({ asThrowawayUser: true });

test.describe('Remote MCP tools in chat (JAN-343)', () => {
  const fake = new FakeIntegrations();
  const stub = new LLMStub();

  test.beforeAll(async () => {
    await fake.start();
    await stub.start();
  });
  test.afterAll(async () => {
    await stub.stop();
    await fake.stop();
  });
  test.beforeEach(async ({ adminRequest }) => {
    await stub.useAsBackendModel(adminRequest);
  });
  test.afterEach(async ({ adminRequest }) => {
    await LLMStub.resetBackendModel(adminRequest);
  });

  /** The tool calls the fake MCP server received, as `tools/call` params, oldest first. */
  const upstreamCalls = (): { name: string; arguments: Record<string, unknown> }[] =>
    fake
      .requestsTo('/mcp')
      .map((request) => (request.body ? (JSON.parse(request.body) as { method?: string; params?: unknown }) : {}))
      .filter((message) => message.method === 'tools/call')
      .map((message) => message.params as { name: string; arguments: Record<string, unknown> });

  /** Registers the fake server for this user, discovers it, and enables it; returns the chat tool names. */
  const registerServer = async (page: Page) => {
    const created = (await (
      await api(page, 'post', '/api/tools/servers', {
        provider_name: PROVIDER,
        endpoint: `${fake.url}/mcp`,
        access_token: FAKE_MCP_ACCESS_TOKEN,
      })
    ).json()) as { id: number };
    await api(page, 'post', `/api/tools/servers/${created.id}/discover`);
    await api(page, 'patch', `/api/tools/servers/${created.id}`, { enabled: true });
    return {
      lookup: `mcp_${created.id}_e2e_fetch_record`,
      update: `mcp_${created.id}_e2e_update_record`,
    };
  };

  /**
   * Scripts the model: it asks for the lookup or the update when the user asks for it, and
   * answers after the tool's result (or refusal) with a line that names what happened.
   */
  const scriptModel = (tools: { lookup: string; update: string }) =>
    stub.reply((request: StubRequest) => {
      if (isBriefingRequest(request)) return { chunks: ['Stub briefing.'] };
      const messages = request.messages;
      const userIndex = messages.map((message) => message.role).lastIndexOf('user');
      const toolResult = messages.slice(userIndex + 1).find((message) => message.role === 'tool');
      if (toolResult) {
        if (toolResult.content.includes('E2E record 42')) return { chunks: ['Record 42 is paid.'] };
        if (toolResult.content.includes('E2E record updated')) return { chunks: ['The due date is cleared.'] };
        return { chunks: ['Okay, I did not change it.'] }; // the BLOCKED refusal
      }
      const asked = lastUserMessage(request);
      if (asked.includes('Look up record 42')) {
        return { chunks: [], toolCalls: [{ name: tools.lookup, arguments: { id: '42' } }] };
      }
      if (asked.includes('Clear the due date on row 7')) {
        return { chunks: [], toolCalls: [{ name: tools.update, arguments: { id: 'row-7', due_date: null } }] };
      }
      return { chunks: ['(unexpected)'] };
    });

  const openChat = async (page: Page) => {
    await trackChatSocketOpen(page);
    await page.goto('/');
    const chat = page.getByRole('complementary', { name: 'Chat with Janus' });
    const input = chat.getByPlaceholder(PLACEHOLDER);
    await expect(input).toBeVisible();
    await waitForOpenChatSocket(page);
    return { chat, input };
  };

  test('a read-only remote tool runs without asking and its result is answered', async ({ page }) => {
    const tools = await registerServer(page);
    scriptModel(tools);
    const { chat, input } = await openChat(page);
    const before = upstreamCalls().length;

    await input.fill('Look up record 42');
    await input.press('Enter');

    await expect(chat.getByText('Record 42 is paid.')).toBeVisible();
    await expect(chat.getByRole('group', { name: /^Janus wants to use/ })).toHaveCount(0);
    await expect(chat.getByRole('button', { name: new RegExp(`^${tools.lookup}, done`) })).toBeVisible();
    expect(upstreamCalls().slice(before)).toEqual([{ name: 'e2e_fetch_record', arguments: { id: '42' } }]);
  });

  test('approving a write call sends exactly the arguments the card showed', async ({ page }) => {
    const tools = await registerServer(page);
    scriptModel(tools);
    const { chat, input } = await openChat(page);
    const before = upstreamCalls().length;

    await input.fill('Clear the due date on row 7');
    await input.press('Enter');

    const card = chat.getByRole('group', { name: `Janus wants to use ${PROVIDER}: e2e_update_record` });
    await expect(card).toBeVisible();
    // The explicit null is shown, not dropped, and nothing the model left out is added.
    await expect(card.getByText(/"due_date": null/)).toBeVisible();
    await expect(card.getByText(/"id": "row-7"/)).toBeVisible();
    expect(upstreamCalls().slice(before)).toEqual([]);

    await card.getByRole('button', { name: 'Approve' }).click();
    await expect(chat.getByText('The due date is cleared.')).toBeVisible();
    await expect(card).toHaveCount(0);
    await expect(chat.getByRole('button', { name: new RegExp(`^${tools.update}, done, approved by you`) })).toBeVisible();
    expect(upstreamCalls().slice(before)).toEqual([
      { name: 'e2e_update_record', arguments: { id: 'row-7', due_date: null } },
    ]);
  });

  test('denying a write call sends nothing upstream and the model says so', async ({ page }) => {
    const tools = await registerServer(page);
    scriptModel(tools);
    const { chat, input } = await openChat(page);
    const before = upstreamCalls().length;

    await input.fill('Clear the due date on row 7');
    await input.press('Enter');

    const card = chat.getByRole('group', { name: `Janus wants to use ${PROVIDER}: e2e_update_record` });
    await expect(card).toBeVisible();
    await card.getByRole('button', { name: 'Deny' }).click();

    await expect(chat.getByText('Okay, I did not change it.')).toBeVisible();
    await expect(card).toHaveCount(0);
    await expect(chat.getByRole('button', { name: new RegExp(`^${tools.update}, blocked, denied by you`) })).toBeVisible();
    expect(upstreamCalls().slice(before)).toEqual([]);
  });
});
