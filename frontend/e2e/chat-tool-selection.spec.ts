import { type Page } from '@playwright/test';
import { runBackendPython } from './backendSeed';
import { expect, test } from './fixtures';
import { api, trackChatSocketOpen, waitForOpenChatSocket } from './helpers';
import { isBriefingRequest, LLMStub, type StubRequest } from './llm-stub';

// JAN-300: brain-selected, task-scoped toolsets. Before each chat message the backend
// asks the utility model (here the OpenAI-compatible stub) which authorized toolboxes
// the message needs, narrows the offered tools to that choice, and streams the choice
// and its reason in __TOOL_SELECTION__. Ids the account may not use are trimmed. An
// ad-hoc trigger run records the same choice in its step trace on the Agent Tasks page.
// Every test runs as its own throwaway user (JAN-336); only the instance-wide backend
// model is changed through the shared admin, and reset after each test.

const PLACEHOLDER = 'Ask a question...';

const sentByUser = (request: StubRequest, text: string) =>
  request.messages.some((message) => message.role === 'user' && message.content.includes(text));

const openChat = async (page: Page) => {
  await trackChatSocketOpen(page);
  await page.goto('/');
  const chat = page.getByRole('complementary', { name: 'Chat with Janus' });
  const input = chat.getByPlaceholder(PLACEHOLDER);
  await expect(input).toBeVisible();
  await waitForOpenChatSocket(page);
  return { chat, input };
};

test.use({ asThrowawayUser: true });

test.describe('Brain-selected tools in chat (JAN-300)', () => {
  const stub = new LLMStub();
  test.beforeAll(() => stub.start());
  test.afterAll(() => stub.stop());
  test.beforeEach(async ({ adminRequest }) => {
    await stub.useAsBackendModel(adminRequest);
  });
  test.afterEach(async ({ adminRequest }) => {
    stub.selectTools(null);
    await LLMStub.resetBackendModel(adminRequest);
  });

  test('offers only the selected tools, shows why, and drops ids the account may not use', async ({ page }) => {
    stub.selectTools({ tool_ids: ['topics', 'mail.send'], reason: 'The user asks about tracked topics.' });
    stub.reply((request) =>
      isBriefingRequest(request)
        ? { chunks: ['Stub briefing.'] }
        : { chunks: [sentByUser(request, 'Which topics do I track?') ? 'You track two topics.' : '(unexpected)'] },
    );
    const { chat, input } = await openChat(page);

    await input.fill('Which topics do I track?');
    await input.press('Enter');
    await expect(chat.getByText('You track two topics.')).toBeVisible();

    const note = chat.getByRole('note', { name: 'Tool selection' });
    await expect(note).toContainText(/Tools picked for this message \(3 of \d+\): core, topics, docs/);
    await expect(note).toContainText('Why: The user asks about tracked topics.');
    // mail.send is not available to this account (no mailbox), so the selection cannot add it.
    await expect(note).toContainText('Not available here, dropped: mail.send');

    // The model was offered the selected toolboxes, plus request_tools to add a left-out one.
    const chatRequest = stub.requests.find((request) => sentByUser(request, 'Which topics do I track?'));
    expect(chatRequest?.toolNames).toEqual(expect.arrayContaining(['list_topics', 'list_docs', 'request_tools']));
    expect(chatRequest?.toolNames).not.toContain('add_memory_fact');
    expect(chatRequest?.toolNames).not.toContain('send_email');
  });

  test('an empty selection falls back to every authorized tool and shows no selection note', async ({ page }) => {
    stub.reply((request) =>
      isBriefingRequest(request)
        ? { chunks: ['Stub briefing.'] }
        : { chunks: [sentByUser(request, 'Anything new?') ? 'Nothing new.' : '(unexpected)'] },
    );
    const { chat, input } = await openChat(page);

    await input.fill('Anything new?');
    await input.press('Enter');
    await expect(chat.getByText('Nothing new.')).toBeVisible();
    await expect(chat.getByRole('note', { name: 'Tool selection' })).toHaveCount(0);
    const chatRequest = stub.requests.find((request) => sentByUser(request, 'Anything new?'));
    expect(chatRequest?.toolNames).toEqual(expect.arrayContaining(['list_topics', 'add_memory_fact', 'list_docs']));
    expect(chatRequest?.toolNames).not.toContain('request_tools');
  });
});

/**
 * Runs one queued trigger through the real handler (selection, authorization, result
 * recording); only the utility model's selection and the agent loop's answer are scripted.
 */
const RUN_TRIGGER_WITH_SCRIPTED_SELECTION = `
import json
from langchain_core.messages import AIMessage
from janus import toolset_selection
from janus.agent_task_runner import AgentTaskRun
from janus.trigger_runtime import build_runtime_trigger_handler
from janus.trigger_service import _envelope_from_record

class SelectionModel:
    def invoke(self, messages):
        return AIMessage(content=json.dumps({"tool_ids": ["topics", "mail.send"], "reason": "Only tracked topics matter here."}))

class UtilityService:
    llm = SelectionModel()

toolset_selection.utility_service = lambda *a, **k: UtilityService()

def answer(db, system_prompt, tools, objective):
    print("offered:", ",".join(sorted(tool.name for tool in tools)))
    return AgentTaskRun(output="No tracked topic needs you today.")

row = db.query(models.RuntimeTrigger).filter(models.RuntimeTrigger.trigger_id == args[0], models.RuntimeTrigger.user_id == user.id).one()
build_runtime_trigger_handler(database.SessionLocal, task_executor=answer)(_envelope_from_record(row))
`;

test('an ad-hoc trigger run shows its tool selection in the Agent Tasks step trace', async ({ page, account }) => {
  const source = `e2e-toolset-${Date.now()}`;
  const response = await api(page, 'post', '/api/runtime/triggers/', {
    task_id: `task-${source}`,
    idempotency_key: `key-${source}`,
    kind: 'manual',
    source,
    objective: 'Tell me whether any tracked topic needs me today.',
  });
  const { trigger_id: triggerId } = (await response.json()) as { trigger_id: string };

  const output = await runBackendPython(RUN_TRIGGER_WITH_SCRIPTED_SELECTION, [triggerId], { as: account.username });
  // The agent loop was offered the selected toolboxes only.
  expect(output).toContain('list_topics');
  expect(output).not.toContain('add_memory_fact');
  expect(output).not.toContain('send_email');

  await page.goto(`/tasks?kind=trigger&task=${triggerId}`);
  const trace = page.getByTestId('task-detail').getByTestId('task-step-trace');
  await expect(trace).toContainText(/Tool selection: selected, 3 of \d+ offered \(core, topics, docs\)/);
  await expect(trace).toContainText('trimmed: mail.send. Why: Only tracked topics matter here.');
  await expect(trace.getByText('topics', { exact: true })).toBeVisible();
});
