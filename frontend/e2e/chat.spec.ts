import { type Page } from '@playwright/test';
import { UI_VERSION_HTML_CLASSES, UI_VERSIONS, type UIVersion } from '../src/contexts/uiVersion';
import { expect, test } from './fixtures';
import { answerConfirm, api, confirmDialog, deferred, trackChatSocketOpen, waitForOpenChatSocket } from './helpers';
import { isBriefingRequest, LLMStub, type StubReply, type StubRequest } from './llm-stub';

// JAN-248: one tab-wide chat session. The sidebar chat and the /chat page are two
// views of the same session, backed by exactly one /ws/chat socket per tab. This
// account has no LLM provider, so the "Chat session" tests send no message (a reply
// would fail); they exercise the session plumbing: socket count, draft, and active
// conversation. The streaming tests below (JAN-220) point the backend at an
// OpenAI-compatible stub (`llm-stub.ts`) and talk to the real `/ws/chat`.

const PLACEHOLDER = 'Ask a question...';

const trackChatSockets = (page: Page) => {
  const urls: string[] = [];
  page.on('websocket', (socket) => {
    if (socket.url().includes('/ws/chat')) urls.push(socket.url());
  });
  return urls;
};

test.describe('Chat session', () => {
  // As a throwaway user (JAN-336): conversations are per user, so nothing here depends on,
  // or adds to, the shared e2e admin's chat history.
  test.use({ asThrowawayUser: true });

  test('keeps one socket and the draft while moving between the sidebar and /chat', async ({ page }) => {
    const chatSockets = trackChatSockets(page);
    await page.goto('/');

    const sidebar = page.getByRole('complementary', { name: 'Chat with Janus' });
    const sidebarInput = sidebar.getByPlaceholder(PLACEHOLDER);
    await expect(sidebarInput).toBeVisible();
    await sidebarInput.fill('Draft that must survive navigation');

    await page.getByRole('link', { name: 'Email Preview' }).click();
    await expect(page).toHaveURL(/\/emails$/);
    await expect(sidebarInput).toHaveValue('Draft that must survive navigation');

    await page.getByRole('link', { name: 'Chat', exact: true }).click();
    await expect(page).toHaveURL(/\/chat$/);
    await expect(page.getByTestId('chat-page')).toBeVisible();
    // The /chat page is the only chat view there: no sidebar copy of the same session.
    await expect(sidebar).toHaveCount(0);
    await expect(page.getByPlaceholder(PLACEHOLDER)).toHaveCount(1);
    await expect(page.getByPlaceholder(PLACEHOLDER)).toHaveValue('Draft that must survive navigation');

    await page.getByRole('link', { name: 'Dashboard' }).click();
    await expect(sidebarInput).toHaveValue('Draft that must survive navigation');

    expect(chatSockets).toHaveLength(1);
  });

  test('a conversation started on /chat stays active in the sidebar and across a reload', async ({ page }) => {
    await page.goto('/chat');
    const chatPage = page.getByTestId('chat-page');
    await expect(chatPage.getByPlaceholder(PLACEHOLDER)).toBeVisible();

    await chatPage.getByRole('button', { name: 'New Chat' }).click();
    const activeOnPage = chatPage.locator('[aria-current="true"]');
    await expect(activeOnPage).toHaveCount(1);
    const activeTitle = (await activeOnPage.innerText()).trim();

    await page.getByRole('link', { name: 'Dashboard' }).click();
    const sidebar = page.getByRole('complementary', { name: 'Chat with Janus' });
    const activeInSidebar = sidebar.locator('[aria-current="true"]');
    await expect(activeInSidebar).toHaveCount(1);
    await expect(activeInSidebar).toHaveText(activeTitle);

    // The active conversation id is persisted for the tab and restored on reload.
    await page.reload();
    await expect(sidebar.locator('[aria-current="true"]')).toHaveText(activeTitle);
  });

  test('deletes a chat from the history only after the in-app confirmation (JAN-292)', async ({ page }) => {
    // Created through the API: this account has no LLM, so it cannot start a chat by talking.
    const title = `E2E chat to delete ${Date.now()}`;
    await api(page, 'post', '/api/conversations/', { title });

    await page.goto('/chat');
    const history = page.getByTestId('chat-page');
    const entry = history.getByText(title, { exact: true });
    await expect(entry).toBeVisible();
    const deleteButton = history.getByRole('button', { name: `Delete chat ${title}`, exact: true });

    await deleteButton.click();
    await expect(confirmDialog(page, 'Delete chat?')).toContainText(`Delete chat "${title}"?`);
    await answerConfirm(page, 'Delete chat?', 'Cancel');
    await expect(entry).toBeVisible();
    await page.reload();
    await expect(entry).toBeVisible();

    await deleteButton.click();
    await answerConfirm(page, 'Delete chat?', 'Delete');
    await expect(entry).toHaveCount(0);
    await page.reload();
    await expect(page.getByTestId('chat-page')).toBeVisible();
    await expect(entry).toHaveCount(0);
  });
});

// JAN-220: chat against the real backend. Only the model's HTTP API is replaced
// (`llm-stub.ts`), so the runtime adapter, the tool selection, the LangChain agent,
// `llm_service.py`, and `websocket/chat.py` all run for real. The `shell` option
// picks the user's app shell through the real preference endpoint; it defaults to
// the classic shell.
const chatTest = test.extend<{ shell: UIVersion }>({
  shell: ['v1', { option: true }],
});

const BRIEFING_TEXT = 'Stub briefing: nothing urgent today.';

/** Answers the dashboard's briefing run with a short text and every chat turn with `chat`. */
const replies =
  (chat: (request: StubRequest) => StubReply) =>
  (request: StubRequest): StubReply =>
    isBriefingRequest(request) ? { chunks: [BRIEFING_TEXT] } : chat(request);

/** Opens the dashboard in the chosen shell and waits until its chat can send. */
const openChat = async (page: Page, shell: UIVersion) => {
  await page.goto('/');
  const html = page.locator('html');
  for (const [version, className] of Object.entries(UI_VERSION_HTML_CLASSES)) {
    if (!className) continue;
    if (version === shell) await expect(html).toHaveClass(new RegExp(`\\b${className}\\b`));
    else await expect(html).not.toHaveClass(new RegExp(`\\b${className}\\b`));
  }
  const chat = page.getByRole('complementary', { name: 'Chat with Janus' });
  const input = chat.getByPlaceholder(PLACEHOLDER);
  await expect(input).toBeVisible();
  await waitForOpenChatSocket(page);
  return { chat, input };
};

const trackChatFrames = (page: Page) => {
  const frames: string[] = [];
  page.on('websocket', (socket) => {
    if (socket.url().includes('/ws/chat')) socket.on('framereceived', (frame) => frames.push(String(frame.payload)));
  });
  return frames;
};

const sentByUser = (request: StubRequest, text: string) =>
  request.messages.some((message) => message.role === 'user' && message.content.includes(text));

chatTest.describe('Chat against the real backend (JAN-220)', () => {
  const stub = new LLMStub();
  chatTest.beforeAll(() => stub.start());
  chatTest.afterAll(() => stub.stop());
  // As the shared admin: the tool selection checked below includes the admin-only `skills`
  // toolbox. The shell preference and the backend model are reset after every test.
  chatTest.use({ asThrowawayUser: false });
  chatTest.beforeEach(async ({ page, shell }) => {
    await api(page, 'patch', '/api/auth/me/ui-preference', { ui_version: shell });
    await trackChatSocketOpen(page);
    await stub.useAsBackendModel(page);
  });
  chatTest.afterEach(async ({ adminRequest }) => {
    await api(adminRequest, 'patch', '/api/auth/me/ui-preference', { ui_version: 'v1' });
    await LLMStub.resetBackendModel(adminRequest);
  });

  chatTest('streams a reply as it arrives and reports the default tool selection', async ({ page, shell }) => {
    const gate = deferred();
    stub.reply(
      replies((request) =>
        sentByUser(request, 'Stream me a reply')
          ? { chunks: ['First half of the streamed reply. ', 'Second half ', 'arrived later.'], gate: gate.promise }
          : { chunks: ['(unexpected request)'] },
      ),
    );
    const frames = trackChatFrames(page);
    const { chat, input } = await openChat(page, shell);

    await input.fill('Stream me a reply');
    await input.press('Enter');

    // The first token is on screen while the model still holds back the rest.
    await expect(chat.getByText('First half of the streamed reply.')).toBeVisible();
    await expect(chat.getByText('arrived later.')).toHaveCount(0);
    await expect(input).toBeDisabled();
    gate.release();
    await expect(chat.getByText('First half of the streamed reply. Second half arrived later.')).toBeVisible();
    await expect(input).toBeEnabled();

    // The tools the backend selected for this turn: this account has no mail, calendar,
    // Jira, GitHub, Linear, or web-search connection, so those are withheld, and the
    // always-granted local tools (plus the admin-only skills) are offered.
    const selectionFrame = frames.find((frame) => frame.startsWith('__TOOL_SELECTION__:'));
    expect(selectionFrame).toBeDefined();
    const selection = JSON.parse(selectionFrame!.slice('__TOOL_SELECTION__:'.length)) as {
      tools: { id: string; confirmation: string }[];
      denied: { tool_id: string; reason: string }[];
    };
    const offered = selection.tools.map((tool) => tool.id);
    expect(offered).toEqual(
      expect.arrayContaining(['core', 'memory', 'topics', 'planner.local', 'knowledge', 'wiki', 'triggers', 'docs', 'skills']),
    );
    const denied = Object.fromEntries(selection.denied.map((denial) => [denial.tool_id, denial.reason]));
    expect(denied).toMatchObject({
      mail: 'disabled_by_context',
      'mail.send': 'disabled_by_context',
      calendar: 'disabled_by_context',
      jira: 'disabled_by_context',
      github: 'missing_capability',
      linear: 'missing_capability',
      web_search: 'missing_capability',
    });
    for (const toolId of Object.keys(denied)) expect(offered).not.toContain(toolId);
    expect(selection.tools.find((tool) => tool.id === 'planner.local.write')?.confirmation).toBe('required');
    // The withheld tools are named above the chat ...
    await expect(chat.getByRole('status').filter({ hasText: 'Tools unavailable this turn:' })).toContainText(
      'mail (disabled by context)',
    );
    // ... and the model was offered the selected tools only, on a streaming request.
    const chatRequest = stub.requests.find((request) => sentByUser(request, 'Stream me a reply'));
    expect(chatRequest?.stream).toBe(true);
    expect(chatRequest?.toolNames).toEqual(expect.arrayContaining(['list_topics', 'list_docs']));
    expect(chatRequest?.toolNames).not.toContain('search_emails');
    expect(chatRequest?.toolNames).not.toContain('list_calendar_events');

    // The streamed reply was stored with the conversation.
    await page.reload();
    await expect(chat.getByText('First half of the streamed reply. Second half arrived later.')).toBeVisible();
  });

  chatTest('a provider failure ends the turn with an error and the chat reconnects', async ({ page, shell }) => {
    stub.reply(replies(() => ({ chunks: [], failStatus: 500 })));
    const { chat, input } = await openChat(page, shell);

    await input.fill('This turn fails');
    await input.press('Enter');

    // The OpenAI client retries a 500 before giving up, so this takes a few seconds.
    await expect(chat.getByText(/its LLM provider failed/)).toBeVisible({ timeout: 30_000 });

    // Reconnecting gives a working chat again once the provider is back.
    stub.reply(replies(() => ({ chunks: ['Back online.'] })));
    await chat.getByRole('button', { name: 'Reconnect' }).click();
    await expect(chat.getByText(/its LLM provider failed/)).toHaveCount(0);
    await expect(input).toBeEnabled();
    await expect.poll(() => page.evaluate(() => (window as unknown as { __e2eOpenChatSockets: number }).__e2eOpenChatSockets)).toBe(2);
    await input.fill('Are you back?');
    await input.press('Enter');
    await expect(chat.getByText('Back online.')).toBeVisible();
  });

  // JAN-212: a reply taller than the chat must scroll inside the chat's transcript;
  // it must not grow the drawer past the viewport and push the composer off screen.
  // One test, run in every shell through the `shell` option.
  const LONG_REPLY_LINES = Array.from(
    { length: 80 },
    (_, index) => `Line ${String(index + 1).padStart(2, '0')} of the long reply.`,
  );

  for (const shell of UI_VERSIONS) {
    chatTest.describe(`in the ${shell} shell`, () => {
      chatTest.use({ shell });

      chatTest('a long streamed reply scrolls inside the chat and keeps the composer on screen', async ({ page }) => {
        const gate = deferred();
        stub.reply(replies(() => ({ chunks: LONG_REPLY_LINES.map((line) => `${line}\n\n`), gate: gate.promise })));
        const { chat, input } = await openChat(page, shell);

        await input.fill('Give me a long answer');
        await input.press('Enter');

        const first = chat.getByText(LONG_REPLY_LINES[0], { exact: true });
        const last = chat.getByText(LONG_REPLY_LINES.at(-1)!, { exact: true });

        // The reply streams in this shell too: its first line renders while the model
        // still holds back the rest, and the composer waits for the turn to end.
        await expect(first).toBeVisible();
        await expect(last).toHaveCount(0);
        await expect(input).toBeDisabled();
        gate.release();

        await expect(last).toBeVisible();
        await expect(input).toBeEnabled();

        // The transcript followed the reply to its end, and the composer stayed on screen.
        await expect(last).toBeInViewport();
        await expect(input).toBeInViewport();
        const transcript = chat.getByRole('region', { name: 'Conversation history' });
        const box = await transcript.evaluate((element) => ({
          scrollHeight: element.scrollHeight,
          clientHeight: element.clientHeight,
          bottom: element.getBoundingClientRect().bottom,
        }));
        expect(box.scrollHeight).toBeGreaterThan(box.clientHeight * 2);
        expect(box.bottom).toBeLessThanOrEqual(page.viewportSize()!.height);

        // The user can scroll back to the start of the reply with the mouse wheel.
        await transcript.hover();
        await page.mouse.wheel(0, -100_000);
        await expect(first).toBeInViewport();
        await expect(last).not.toBeInViewport();
        await expect(input).toBeInViewport();
      });
    });
  }
});
