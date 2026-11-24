import type { Page, Route } from '@playwright/test';
import { E2E_BASE_URL } from './env';
import { expect, test } from './fixtures';
import { api, createThrowawayUser } from './helpers';

/**
 * The Agent Tasks page (JAN-204): persisted trigger runs, automation runs and
 * delegated tasks with their plan steps and tool trace.
 *
 * The e2e backend runs with the trigger worker disabled and no model, so real
 * runs stay queued with no plan. Recorded steps, traces and delegations are
 * served through `page.route` with the exact shape of the backend schema
 * (`src/janus/schemas/runtime_tasks.py`); everything else hits the real API.
 */

const stamp = () => `${Date.now()}-${Math.floor(Math.random() * 1000)}`;

/** Queues an ad-hoc manual trigger run as the page's user and returns its trigger id and source. */
const queueManualRun = async (page: Page) => {
  const source = `e2e-tasks-${stamp()}`;
  const response = await api(page, 'post', '/api/runtime/triggers/', {
    task_id: `task-${source}`,
    idempotency_key: `key-${source}`,
    kind: 'manual',
    source,
    objective: 'Check the inbox for anything urgent.',
  });
  const { trigger_id: triggerId } = await response.json();
  return { triggerId: triggerId as string, source };
};

const summary = {
  source: 'definition',
  ref_id: 'e2e-mock-run',
  task_id: 'definition:9:e2e',
  title: 'E2E weekly review',
  status: 'completed',
  created_at: '2026-10-05T07:30:00Z',
  completed_at: '2026-10-05T07:31:12Z',
  plan_status: 'completed',
  step_count: 1,
  completed_step_count: 1,
  failed_step_count: 0,
  current_step: null,
  definition_id: 9,
  definition_name: 'E2E weekly review',
  conversation_id: null,
  child_count: 1,
};

const childSummary = {
  ...summary,
  source: 'delegated',
  ref_id: 'e2e-mock-child',
  task_id: 'child-e2e',
  title: 'Summarize open invoices',
  definition_id: null,
  definition_name: null,
  child_count: 0,
};

const detailExtras = {
  objective: 'Review the week and list what needs a reply.',
  task_mode: 'automation',
  node: 'complete',
  reason: null,
  trigger_source: 'definition.schedule',
  attempt_count: 1,
  last_error: null,
  allowed_tool_ids: [],
  delegation_result: null,
  delegation_error: null,
  parent: null,
};

const runDetail = {
  ...summary,
  ...detailExtras,
  steps: [
    {
      step_id: 'step-1',
      objective: 'Review the week and list what needs a reply.',
      status: 'completed',
      depends_on: [],
      required_tool_ids: ['mail.read'],
      trace: {
        model: 'ollama/e2e-model',
        authorized_tools: ['mail.read', 'mail.send'],
        invoked_tools: ['search_emails', 'send_email'],
        skipped: null,
        refused: 'send_email (off for unattended runs)',
        failed: null,
        suspected_injection: 'read_email (ignore previous instructions)',
      },
      output: 'Three threads need a reply. I did not send anything.',
      error: null,
    },
  ],
  children: [childSummary],
};

const childDetail = (conversationId: number) => ({
  ...childSummary,
  ...detailExtras,
  objective: 'Summarize open invoices',
  task_mode: 'delegated',
  trigger_source: null,
  attempt_count: null,
  allowed_tool_ids: ['mail.read'],
  delegation_result: 'Two invoices are open.',
  conversation_id: conversationId,
  parent: { kind: 'trigger', ref_id: 'e2e-mock-run' },
  steps: [],
  children: [],
});

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

test.describe('Agent Tasks page (JAN-204)', () => {
  // Every test runs as its own throwaway user (JAN-336): the task list is paginated and
  // newest first, so runs other specs leave on the shared e2e admin never crowd it.
  test.use({ asThrowawayUser: true });

  for (const ui of ['v1', 'v2', 'v3'] as const) {
    test(`is reachable from the primary navigation in the ${ui} shell`, async ({ page }) => {
      await page.goto(`/?ui=${ui}`);
      await page.getByRole('link', { name: 'Agent Tasks' }).first().click();
      await expect(page).toHaveURL(/\/tasks/);
      await expect(page.getByRole('heading', { name: 'Agent Tasks', level: 1 })).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Runs and delegated tasks' })).toBeVisible();
      await expect(page.getByText('Select a task to see its plan steps and tool trace.')).toBeVisible();
      if (ui !== 'v1') await expect(page.locator('html')).toHaveClass(new RegExp(`ui-${ui}`));
    });
  }

  test('lists a queued trigger run, filters it, and opens its detail', async ({ page }) => {
    const { triggerId, source } = await queueManualRun(page);

    await page.goto('/tasks');
    const row = page.getByTestId('task-row').filter({ hasText: source });
    await expect(row).toBeVisible();
    await expect(row).toContainText('Ad-hoc trigger run');
    await expect(row).toContainText('Queued');

    await page.getByRole('combobox', { name: 'Filter tasks by source' }).selectOption('delegated');
    await expect(row).toHaveCount(0);
    await page.getByRole('combobox', { name: 'Filter tasks by source' }).selectOption('trigger');
    await page.getByRole('combobox', { name: 'Filter tasks by status' }).selectOption('queued');
    await expect(row).toBeVisible();
    await page.getByRole('combobox', { name: 'Filter tasks by status' }).selectOption('completed');
    await expect(row).toHaveCount(0);
    await page.getByRole('combobox', { name: 'Filter tasks by status' }).selectOption('');

    await row.click();
    await expect(page).toHaveURL(new RegExp(`kind=trigger&task=${triggerId}`));
    const detail = page.getByTestId('task-detail');
    await expect(detail.getByRole('heading', { name: source })).toBeVisible();
    await expect(detail).toContainText('Queued');
    await expect(detail).toContainText('No plan recorded yet');
    await expect(row).toHaveAttribute('aria-current', 'true');

    // The selection lives in the URL, so a reload reopens the same task.
    await page.reload();
    await expect(page.getByTestId('task-detail').getByRole('heading', { name: source })).toBeVisible();
  });

  test('an automation run links from Recent runs to its task and back to its definition, and keeps its provenance once deleted', async ({ page }) => {
    const name = `E2E tasks automation ${stamp()}`;
    const definition = await (
      await api(page, 'post', '/api/runtime/definitions/', { name, objective: 'Summarize the inbox.', tool_ids: [] })
    ).json();
    const run = await (await api(page, 'post', `/api/runtime/definitions/${definition.id}/run`)).json();
    let deleted = false;
    try {
      await page.goto('/settings?tab=runtime');
      const widget = page.locator(`[data-trigger-id="${run.trigger_id}"]`);
      await widget.getByRole('link', { name: 'Plan steps and tool trace' }).click();
      await expect(page).toHaveURL(new RegExp(`/tasks\\?kind=trigger&task=${run.trigger_id}`));

      const detail = page.getByTestId('task-detail');
      await expect(detail.getByRole('heading', { name })).toBeVisible();
      await expect(detail).toContainText('Automation run');
      await expect(page.getByTestId('task-row').filter({ hasText: name })).toContainText('Automation run');

      await detail.getByRole('link', { name: new RegExp(`Open definition “${name}”`) }).click();
      await expect(page).toHaveURL(/\/settings\?tab=runtime/);
      await expect(page.locator('[data-testid^="trigger-definition-"]').filter({ hasText: name }).first()).toBeVisible();

      // Deleting the definition keeps the run's provenance: still an automation run under its name, no link.
      await api(page, 'delete', `/api/runtime/definitions/${definition.id}`);
      deleted = true;
      await page.goto(`/tasks?kind=trigger&task=${run.trigger_id}`);
      const after = page.getByTestId('task-detail');
      await expect(after.getByRole('heading', { name })).toBeVisible();
      await expect(after).toContainText('Automation run');
      await expect(after.getByRole('link', { name: /Open definition/ })).toHaveCount(0);
      await page.getByRole('combobox', { name: 'Filter tasks by source' }).selectOption('definition');
      await expect(page.getByTestId('task-row').filter({ hasText: name })).toBeVisible();
    } finally {
      if (!deleted) await api(page, 'delete', `/api/runtime/definitions/${definition.id}`);
    }
  });

  test('a task that does not exist or belongs to someone else reads as not found', async ({ page }) => {
    await page.goto('/tasks?kind=trigger&task=no-such-run');
    await expect(page.getByRole('alert')).toHaveText('This task does not exist or is not yours.');
    await page.goto('/tasks?kind=delegated&task=no-such-delegation');
    await expect(page.getByRole('alert')).toHaveText('This task does not exist or is not yours.');
  });

  test('shows recorded plan steps and tool trace, and links delegated tasks to their run and chat', async ({ page }) => {
    const conversationTitle = `E2E delegating chat ${stamp()}`;
    const conversation = await (await api(page, 'post', '/api/conversations/', { title: conversationTitle })).json();
    await page.route('**/api/runtime/tasks/triggers/e2e-mock-run', (route) => json(route, runDetail));
    await page.route('**/api/runtime/tasks/delegations/e2e-mock-child', (route) => json(route, childDetail(conversation.id)));
    try {
      await page.goto('/tasks?kind=trigger&task=e2e-mock-run');
      const detail = page.getByTestId('task-detail');
      await expect(detail.getByRole('heading', { name: 'E2E weekly review' })).toBeVisible();
      const step = detail.getByTestId('task-step');
      await expect(step).toHaveAttribute('data-step-status', 'completed');
      const trace = step.getByTestId('task-step-trace');
      await expect(trace).toContainText('ollama/e2e-model');
      await expect(trace.getByText('search_emails', { exact: true })).toBeVisible();
      await expect(trace).toContainText('Refused: send_email (off for unattended runs)');
      await expect(trace).toContainText('Suspected prompt injection: read_email (ignore previous instructions)');
      await expect(step.getByText('Three threads need a reply.')).toBeHidden();
      await step.getByText('Answer').click();
      await expect(step.getByText('Three threads need a reply. I did not send anything.')).toBeVisible();

      await detail.getByRole('list', { name: 'Delegated tasks' }).getByRole('link', { name: 'Summarize open invoices' }).click();
      await expect(page).toHaveURL(/kind=delegated&task=e2e-mock-child/);
      await expect(page.getByTestId('task-detail')).toContainText('Two invoices are open.');
      await expect(page.getByTestId('task-detail')).toContainText('No plan recorded yet');

      await page.getByRole('link', { name: 'Open the run that delegated it' }).click();
      await expect(page).toHaveURL(/kind=trigger&task=e2e-mock-run/);
      await page.goBack();

      await page.getByRole('link', { name: 'Open the chat that delegated it' }).click();
      await expect(page).toHaveURL(/\/chat$/);
      const chat = page.getByTestId('chat-page');
      await expect(chat.locator('[aria-current="true"]')).toHaveText(conversationTitle);
    } finally {
      await api(page, 'delete', `/api/conversations/${conversation.id}`);
    }
  });

  test('pages through more tasks than fit on one page', async ({ page }) => {
    const items = Array.from({ length: 20 }, (_, index) => ({ ...summary, ref_id: `e2e-page-${index}`, title: `E2E task ${index}` }));
    const offsets: string[] = [];
    await page.route(
      (url) => url.pathname === '/api/runtime/tasks/',
      (route) => {
        const offset = new URL(route.request().url()).searchParams.get('offset') ?? '0';
        offsets.push(offset);
        const shown = offset === '0' ? items : items.slice(0, 5).map((item) => ({ ...item, ref_id: `${item.ref_id}-older`, title: `${item.title} (older)` }));
        return json(route, { items: shown, total: 25, limit: 20, offset: Number(offset), has_more: offset === '0' });
      }
    );
    await page.goto('/tasks');
    const pagination = page.getByTestId('task-pagination');
    await expect(pagination).toContainText('1–20 of 25');
    await expect(page.getByRole('button', { name: 'Newer' })).toBeDisabled();
    await page.getByRole('button', { name: 'Older' }).click();
    await expect(pagination).toContainText('21–25 of 25');
    await expect(page.getByTestId('task-row')).toHaveCount(5);
    await expect(page.getByRole('button', { name: 'Older' })).toBeDisabled();
    expect(offsets).toContain('20');
    await page.getByRole('button', { name: 'Newer' }).click();
    await expect(pagination).toContainText('1–20 of 25');
  });

  test('says so when the task list cannot load', async ({ page }) => {
    await page.route(
      (url) => url.pathname === '/api/runtime/tasks/',
      (route) => json(route, { detail: 'boom' }, 500)
    );
    await page.goto('/tasks');
    await expect(page.getByRole('alert')).toHaveText('Could not load your tasks. Try again in a moment.', { timeout: 20_000 });
  });
  test('says when there are no tasks yet, and when no task matches the filters', async ({ page }) => {
    await page.route(
      (url) => url.pathname === '/api/runtime/tasks/',
      (route) => json(route, { items: [], total: 0, limit: 20, offset: 0, has_more: false })
    );
    await page.goto('/tasks');
    await expect(
      page.getByText('No trigger runs or delegated tasks yet. They appear here as soon as an automation runs or Janus delegates work.')
    ).toBeVisible();
    await expect(page.getByTestId('task-row')).toHaveCount(0);
    await expect(page.getByTestId('task-pagination')).toHaveCount(0);

    await page.getByRole('combobox', { name: 'Filter tasks by status' }).selectOption('failed');
    await expect(page.getByText('No tasks match these filters.')).toBeVisible();
  });

  test('shows a failed run with its failed step and the recorded error', async ({ page }) => {
    const failedSummary = {
      ...summary,
      ref_id: 'e2e-failed-run',
      task_id: 'definition:9:failed',
      title: 'E2E failed review',
      status: 'failed',
      plan_status: 'failed',
      step_count: 2,
      completed_step_count: 1,
      failed_step_count: 1,
      child_count: 0,
    };
    const failedDetail = {
      ...failedSummary,
      ...detailExtras,
      node: 'fail',
      attempt_count: 3,
      last_error: 'Model request timed out after 3 attempts',
      reason: 'Step 2 failed',
      steps: [
        { ...runDetail.steps[0], step_id: 'step-1' },
        {
          step_id: 'step-2',
          objective: 'Draft the replies.',
          status: 'failed',
          depends_on: ['step-1'],
          required_tool_ids: [],
          trace: null,
          output: null,
          error: 'Model request timed out',
        },
      ],
      children: [],
    };
    await page.route(
      (url) => url.pathname === '/api/runtime/tasks/',
      (route) => json(route, { items: [failedSummary], total: 1, limit: 20, offset: 0, has_more: false })
    );
    await page.route('**/api/runtime/tasks/triggers/e2e-failed-run', (route) => json(route, failedDetail));

    await page.goto('/tasks');
    const row = page.getByTestId('task-row').filter({ hasText: 'E2E failed review' });
    await expect(row.locator('[data-status="failed"]')).toHaveText('Failed');
    await expect(row).toContainText('1 failed');

    await row.click();
    await expect(page).toHaveURL(/kind=trigger&task=e2e-failed-run/);
    const detail = page.getByTestId('task-detail');
    await expect(detail.getByRole('heading', { name: 'E2E failed review' })).toBeVisible();
    await expect(detail.getByText('Last error: Model request timed out after 3 attempts')).toBeVisible();
    await expect(detail).toContainText('Plan note: Step 2 failed');
    const steps = detail.getByTestId('task-step');
    await expect(steps).toHaveCount(2);
    await expect(steps.nth(0)).toHaveAttribute('data-step-status', 'completed');
    await expect(steps.nth(1)).toHaveAttribute('data-step-status', 'failed');
    await expect(steps.nth(1).getByRole('alert')).toHaveText('Error: Model request timed out');
  });

  test("never shows another user's tasks, in the list or by direct link", async ({ page, playwright, adminRequest }) => {
    const { username, password } = await createThrowawayUser(adminRequest, 'e2e-tasks-other');
    const other = await playwright.request.newContext({ baseURL: E2E_BASE_URL });
    try {
      const login = await other.post('/api/auth/login', { data: { username, password } });
      expect(login.ok(), `login as ${username} -> ${login.status()}`).toBeTruthy();
      const { csrf_token: csrfToken } = await (await other.get('/api/auth/csrf')).json();
      const otherSource = `e2e-tasks-foreign-${stamp()}`;
      const queued = await other.post('/api/runtime/triggers/', {
        data: {
          task_id: `task-${otherSource}`,
          idempotency_key: `key-${otherSource}`,
          kind: 'manual',
          source: otherSource,
          objective: 'Private work of another user.',
        },
        headers: { 'X-CSRF-Token': csrfToken },
      });
      expect(queued.ok(), `queue as ${username} -> ${queued.status()}`).toBeTruthy();
      const { trigger_id: foreignId } = await queued.json();

      // The other user does see their own run through the same API.
      const own = await (await other.get('/api/runtime/tasks/?limit=100')).json();
      expect(own.items.map((item: { ref_id: string }) => item.ref_id)).toContain(foreignId);

      // This user's own newer run is listed; the other user's run is not.
      const { source: mySource } = await queueManualRun(page);
      const mine = await (await page.request.get('/api/runtime/tasks/?limit=100')).json();
      expect(mine.items.map((item: { ref_id: string }) => item.ref_id)).not.toContain(foreignId);

      await page.goto('/tasks');
      await expect(page.getByTestId('task-row').filter({ hasText: mySource })).toBeVisible();
      await expect(page.getByTestId('task-row').filter({ hasText: otherSource })).toHaveCount(0);

      await page.goto(`/tasks?kind=trigger&task=${foreignId}`);
      await expect(page.getByRole('alert')).toHaveText('This task does not exist or is not yours.');
      await expect(page.getByText(otherSource)).toHaveCount(0);
    } finally {
      await other.dispose();
      await api(adminRequest, 'delete', `/api/auth/users/${username}`);
    }
  });
});
