import { type Page } from '@playwright/test';
import { runBackendPython } from './backendSeed';
import { expect, test } from './fixtures';
import { createThrowawayUser, signInAs, type E2EUser } from './helpers';

/**
 * Drives one ad-hoc trigger run through the real JAN-207 path: the real trigger
 * handler and durable dispatcher, the real `delegation` toolbox and plan graph,
 * and the real delegation runtime. The e2e account has no LLM, so only the two
 * agent loops are stand-ins: the parent delegates a task and waits for it, and
 * the continuation (whose objective carries the child's result) answers.
 *
 * `park <task>` runs the trigger until it parks `waiting` and leaves its child
 * queued (the runner only records it). `finish <task>` runs that child, which
 * releases the parent, then dispatches the parent again so it resumes.
 * `fail <task>` does the same, but the child fails.
 */
const SCRIPT = `
from datetime import UTC, datetime
from janus import delegation_runtime
from janus.agent_task_runner import AgentTaskRun, AgentToolCall
from janus.background_tasks import TaskHandle
from janus.trigger_runtime import build_runtime_trigger_handler
from janus.trigger_service import DurableTriggerDispatcher
from janus.triggers import TriggerEnvelope, TriggerKind

mode, task_id = args[0], args[1]


class RecordOnly:
    def submit(self, name, fn, *, key=None):
        return TaskHandle(name=name, key=key)

    def status(self, key):
        return None


def parent(db, system_prompt, tools, objective):
    by_name = {tool.name: tool for tool in tools}
    if "Resuming after delegated tasks" in objective:
        heard = "the lookup failed" if "failed:" in objective else "the lookup said: sunny in Berlin"
        return AgentTaskRun(output=f"Final answer: {heard}.", tool_calls=())
    created = by_name["delegate_task"].invoke({"objective": "Look up the weather in Berlin"})
    by_name["wait_for_delegated_tasks"].invoke({"delegation_ids": [created["delegation_id"]]})
    return AgentTaskRun(
        output="Asked a background task for the weather; continuing once it reports back.",
        tool_calls=(AgentToolCall(tool="delegate_task", result="ok"), AgentToolCall(tool="wait_for_delegated_tasks", result="ok")),
    )


def child(db, system_prompt, tools, objective):
    if mode == "fail":
        raise RuntimeError("weather service unreachable")
    return AgentTaskRun(output="Sunny in Berlin.", tool_calls=())


owner_id = user.id
envelope = TriggerEnvelope(
    trigger_id=f"trigger-{task_id}",
    idempotency_key=f"key-{task_id}",
    task_id=task_id,
    kind=TriggerKind.MANUAL,
    source="e2e",
    payload={"objective": "Tell me the weather in Berlin"},
    created_at=datetime.now(UTC),
    owner_id=owner_id,
)
dispatcher = DurableTriggerDispatcher(
    database.SessionLocal, build_runtime_trigger_handler(database.SessionLocal, task_executor=parent)
)
if mode == "park":
    delegation_runtime.DELEGATION_RUNNER = RecordOnly()
    print(dispatcher.dispatch(envelope).status.value)
else:
    child_id = (
        db.query(models.RuntimeDelegation.id)
        .filter(models.RuntimeDelegation.parent_task_id == task_id)
        .one()[0]
    )
    db.rollback()
    delegation_runtime.run_delegation(database.SessionLocal, child_id, task_executor=child)
    print(dispatcher.dispatch(envelope).status.value)
`;

const drive = async (owner: E2EUser, mode: 'park' | 'finish' | 'fail', taskId: string): Promise<string> =>
  (await runBackendPython(SCRIPT, [mode, taskId], { as: owner.username })).trim().split('\n').pop() ?? '';

const STAMP = Date.now();
const RESUMED = `e2e-wait-resumed-${STAMP}`;
const STILL_WAITING = `e2e-wait-pending-${STAMP}`;
const CHILD_FAILED = `e2e-wait-child-failed-${STAMP}`;

const runCard = (page: Page, taskId: string) => page.getByTestId('trigger-widget').filter({ hasText: taskId });

const showStatus = async (page: Page, status: string) => {
  const filtered = page.waitForResponse(
    (response) =>
      response.url().includes('/api/runtime/triggers/') &&
      (status === '' ? !response.url().includes('status=') : response.url().includes(`status=${status}`)) &&
      response.ok()
  );
  await page.getByRole('combobox', { name: 'Filter runs by status' }).selectOption(status);
  await filtered;
};

// The runs belong to one throwaway user the tests share (JAN-336), so Recent runs lists only
// these three, never the runs other specs leave on the shared e2e admin.
test.describe('Delegation wait and resume (JAN-207)', () => {
  let owner: E2EUser;

  test.beforeAll(async ({ adminRequest }) => {
    owner = await createThrowawayUser(adminRequest, 'e2e-delegation-wait');
    for (const taskId of [RESUMED, STILL_WAITING, CHILD_FAILED]) {
      expect(await drive(owner, 'park', taskId)).toBe('waiting');
    }
  });

  test.beforeEach(async ({ page }) => {
    await signInAs(page, owner);
  });

  test('a run that waits for its delegated task shows Waiting, then Completed once the task finished', async ({ page }) => {
    await page.goto('/settings?tab=runtime');
    await expect(page.getByRole('heading', { name: 'Recent runs' })).toBeVisible();

    // The filter offers the new status and the backend accepts it.
    await showStatus(page, 'waiting');
    const waiting = runCard(page, RESUMED);
    await expect(waiting).toHaveAttribute('data-status', 'waiting');
    await expect(waiting.getByTestId('trigger-status-badge')).toHaveText('Waiting');
    await expect(waiting.getByTestId('trigger-waiting-note')).toHaveText(
      'waiting for the tasks it delegated; resumes automatically when they finish'
    );
    // What it did before parking is shown; a parked run is not a failure.
    await expect(waiting.getByTestId('trigger-result')).toContainText('Tools invoked: delegate_task, wait_for_delegated_tasks');
    await expect(waiting.getByTestId('trigger-result')).toContainText('continuing once it reports back');
    await expect(waiting.getByTestId('trigger-last-error')).toHaveCount(0);

    // The delegated task finishes; the run resumes with its result.
    expect(await drive(owner, 'finish', RESUMED)).toBe('completed');
    await page.reload();
    await showStatus(page, 'completed');
    const done = runCard(page, RESUMED);
    await expect(done).toHaveAttribute('data-status', 'completed');
    await expect(done.getByTestId('trigger-status-badge')).toHaveText('Completed');
    await expect(done.getByTestId('trigger-result')).toContainText('Final answer: the lookup said: sunny in Berlin.');
    await expect(done.getByTestId('trigger-waiting-note')).toHaveCount(0);
    // Parked once, resumed once.
    await expect(done).toContainText('2 attempts');
  });

  test('a run whose delegated task is still going stays Waiting, and only waiting runs match the filter', async ({ page }) => {
    await page.goto('/settings?tab=runtime');
    await showStatus(page, 'waiting');

    await expect(runCard(page, STILL_WAITING)).toHaveAttribute('data-status', 'waiting');
    await expect(runCard(page, RESUMED)).toHaveCount(0);
    for (const status of await page.getByTestId('trigger-widget').evaluateAll((cards) => cards.map((card) => card.getAttribute('data-status')))) {
      expect(status).toBe('waiting');
    }

    // Clearing the filter lists it with every other run.
    await showStatus(page, '');
    await expect(runCard(page, STILL_WAITING)).toHaveAttribute('data-status', 'waiting');
    await expect(runCard(page, RESUMED)).toHaveAttribute('data-status', 'completed');
  });

  test('a delegated task that fails resumes the run, which completes and reports the failure', async ({ page }) => {
    expect(await drive(owner, 'fail', CHILD_FAILED)).toBe('completed');

    await page.goto('/settings?tab=runtime');
    await showStatus(page, 'completed');
    const card = runCard(page, CHILD_FAILED);
    await expect(card).toHaveAttribute('data-status', 'completed');
    await expect(card.getByTestId('trigger-result')).toContainText('Final answer: the lookup failed.');
    await expect(card.getByTestId('trigger-last-error')).toHaveCount(0);
  });
});
