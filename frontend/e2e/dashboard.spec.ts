import type { Locator, Page } from '@playwright/test';
import { expect, test } from './fixtures';
import { api, createThrowawayUser, deferred, signInAs } from './helpers';

// Every test runs as its own throwaway user (JAN-336, JAN-225): widget preferences, tasks and
// events are per user, so the empty states below are this user's own, and hiding a widget never
// reaches the shared e2e admin's dashboard that other specs (Topic Focus, WhatsApp) look at.
test.use({ asThrowawayUser: true });

test.describe('Dashboard page', () => {
  test('renders all widgets with their empty states for a fresh account', async ({ page }) => {
    await page.goto('/');

    await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();

    await expect(page.getByTestId('dashboard-widget-briefing')).toBeVisible();
    await expect(page.getByTestId('dashboard-widget-topic_focus')).toBeVisible();

    const importantEmailsWidget = page.getByTestId('dashboard-widget-important_emails');
    await expect(importantEmailsWidget.getByText('No important emails at the moment.')).toBeVisible();

    const whatsappWidget = page.getByTestId('dashboard-widget-whatsapp_messages');
    await expect(whatsappWidget.getByText('No recent WhatsApp messages yet.')).toBeVisible();

    const scheduleWidget = page.getByTestId('dashboard-widget-schedule');
    await expect(scheduleWidget.getByText('Nothing on your calendar today.')).toBeVisible();

    const tasksWidget = page.getByTestId('dashboard-widget-tasks');
    await expect(tasksWidget.getByText('All caught up!')).toBeVisible();

    await expect(page.getByRole('heading', { name: 'Planner Suggestions' })).toBeVisible();
    await expect(page.getByText('No pending planner suggestions.')).toBeVisible();
  });

  test('adds a local task from the Active Tasks widget', async ({ page }) => {
    const taskTitle = `E2E dashboard task ${Date.now()}`;

    await page.goto('/');

    const tasksWidget = page.getByTestId('dashboard-widget-tasks');
    await tasksWidget.getByPlaceholder('Add a local task').fill(taskTitle);
    await tasksWidget.getByRole('button', { name: 'Add task' }).click();

    // The create request + query invalidation/refetch round trip can take a bit longer than
    // Playwright's default 5s assertion timeout under load; the task does appear, just later.
    await expect(tasksWidget.getByText(taskTitle)).toBeVisible({ timeout: 15_000 });
  });

  test('hides and re-shows a widget from the widget options menu', async ({ page }) => {
    await page.goto('/');

    const topicFocusWidget = page.getByTestId('dashboard-widget-topic_focus');
    await expect(topicFocusWidget).toBeVisible();

    await page.getByRole('button', { name: 'Configure dashboard widgets' }).click();
    // The toggle's <label> wraps both the widget name and its description, so its
    // accessible name is their concatenation — match by role instead of by exact label text.
    const topicFocusToggle = page.getByRole('checkbox', { name: /^Topic Focus/ });
    await expect(topicFocusToggle).toBeChecked();

    // The checkbox is controlled by an optimistic-update mutation (see Dashboard.tsx's
    // `updateWidgetPreferences`), so its checked state updates a tick after the click
    // rather than synchronously — use a plain click plus a polling assertion instead of
    // `uncheck()`, which expects the state to have already flipped by the time it verifies.
    await topicFocusToggle.click();
    await expect(topicFocusToggle).not.toBeChecked();
    await expect(topicFocusWidget).toHaveCount(0);

    // The widget menu stays open after toggling a checkbox inside it.
    await topicFocusToggle.click();
    await expect(topicFocusToggle).toBeChecked();
    await expect(page.getByTestId('dashboard-widget-topic_focus')).toBeVisible();
  });

  test('nests subtasks under a category heading and updates a nested subtask (JAN-317)', async ({ page }) => {
    const suffix = Date.now();
    const parentTitle = `E2E category ${suffix}`;
    const firstSubtask = `E2E subtask one ${suffix}`;
    const secondSubtask = `E2E subtask two ${suffix}`;
    const parent = await (await api(page, 'post', '/api/planner/local/tasks', { title: parentTitle })).json();
    for (const title of [firstSubtask, secondSubtask]) {
      await api(page, 'post', '/api/planner/local/tasks', { title, parent_task_id: parent.id });
    }

    await page.goto('/');

    const tasksWidget = page.getByTestId('dashboard-widget-tasks');
    const group = tasksWidget.getByTestId(`dashboard-task-group-manual-${parent.id}`);
    // The parent is a category heading with an open count, not a task with its own status control.
    await expect(group).toContainText(parentTitle);
    await expect(group).toContainText('2 open');
    await expect(tasksWidget.getByRole('combobox', { name: `Status of ${parentTitle}` })).toHaveCount(0);

    const subtasks = tasksWidget.getByRole('list', { name: `Subtasks of ${parentTitle}` });
    await expect(subtasks.getByText(firstSubtask)).toBeVisible();
    await expect(subtasks.getByText(secondSubtask)).toBeVisible();

    // Completing a nested subtask drops it from the active list and lowers the heading's count.
    await subtasks.getByRole('combobox', { name: `Status of ${firstSubtask}` }).selectOption('Done');
    await expect(subtasks.getByText(firstSubtask)).toHaveCount(0, { timeout: 15_000 });
    await expect(group).toContainText('1 open');
    await expect(subtasks.getByText(secondSubtask)).toBeVisible();
  });

  test.describe('in UTC', () => {
    // The Schedule widget lists today's events in UTC; pin the browser to UTC so "now" is today there.
    test.use({ timezoneId: 'UTC' });

    test('adds a local event from the Schedule widget (JAN-142)', async ({ page }) => {
      const eventTitle = `E2E dashboard event ${Date.now()}`;
      // Starting now (UTC) and left without an end, which then equals the start.
      const start = new Date().toISOString().slice(0, 16);

      await page.goto('/');
      const scheduleWidget = page.getByTestId('dashboard-widget-schedule');
      await scheduleWidget.getByPlaceholder('Add a local event').fill(eventTitle);
      await scheduleWidget.locator('input[type="datetime-local"]').first().fill(start);

      const created = page.waitForResponse(
        (response) => response.url().endsWith('/api/planner/local/events') && response.request().method() === 'POST'
      );
      await scheduleWidget.getByRole('button', { name: 'Add event' }).click();
      expect((await created).ok()).toBeTruthy();

      await expect(scheduleWidget.getByRole('heading', { name: eventTitle })).toBeVisible({ timeout: 15_000 });
      // The form resets only once the validated answer arrived.
      await expect(scheduleWidget.getByPlaceholder('Add a local event')).toHaveValue('');
    });
  });
});

// JAN-225: widget visibility and collapse are per-user preferences stored by
// `dashboard_service.py`, so they must survive a reload and stay with their user.
// Every test runs as its own throwaway user (see `test.use` above), with a second one for
// the test that checks two users' preferences stay apart.
test.describe('Dashboard widget preferences', () => {
  const savedWidgets = async (page: Page) => {
    const response = await page.request.get('/api/dashboard/widgets');
    expect(response.ok()).toBeTruthy();
    const preferences: Array<{ widget_key: string; is_visible: boolean; is_collapsed: boolean }> = await response.json();
    return Object.fromEntries(preferences.map(({ widget_key, ...rest }) => [widget_key, rest]));
  };

  const widgetSaved = (page: Page) =>
    page.waitForResponse(
      (response) => response.url().endsWith('/api/dashboard/widgets') && response.request().method() === 'PATCH',
    );

  /** Checks a widget card is shown collapsed: its body is gone and its toggle offers Expand. */
  const expectCollapsed = async (widget: Locator, title: string, collapsed: boolean) => {
    await expect(widget).toHaveAttribute('data-collapsed', String(collapsed));
    const toggle = widget.getByRole('button', { name: `${collapsed ? 'Expand' : 'Collapse'} ${title}` });
    await expect(toggle).toHaveAttribute('aria-expanded', String(!collapsed));
  };

  let otherUser: { username: string; password: string };

  test.beforeEach(async ({ adminRequest }) => {
    otherUser = await createThrowawayUser(adminRequest, 'e2e-widgets-other');
  });

  test('a collapsed and a hidden widget stay that way after a reload, and come back when undone', async ({
    page,
  }) => {
    await page.goto('/');
    const tasks = page.getByTestId('dashboard-widget-tasks');
    await expect(tasks.getByPlaceholder('Add a local task')).toBeVisible();

    let saved = widgetSaved(page);
    await tasks.getByRole('button', { name: 'Collapse Active Tasks' }).click();
    expect((await saved).ok()).toBeTruthy();
    await expectCollapsed(tasks, 'Active Tasks', true);
    await expect(tasks.getByPlaceholder('Add a local task')).toHaveCount(0);

    await page.getByRole('button', { name: 'Configure dashboard widgets' }).click();
    saved = widgetSaved(page);
    await page.getByRole('checkbox', { name: /^Topic Focus/ }).click();
    expect((await saved).ok()).toBeTruthy();
    await expect(page.getByTestId('dashboard-widget-topic_focus')).toHaveCount(0);

    expect(await savedWidgets(page)).toMatchObject({
      tasks: { is_visible: true, is_collapsed: true },
      topic_focus: { is_visible: false, is_collapsed: false },
      schedule: { is_visible: true, is_collapsed: false },
    });

    await page.reload();
    await expectCollapsed(page.getByTestId('dashboard-widget-tasks'), 'Active Tasks', true);
    await expect(page.getByTestId('dashboard-widget-tasks').getByPlaceholder('Add a local task')).toHaveCount(0);
    await expect(page.getByTestId('dashboard-widget-topic_focus')).toHaveCount(0);
    // Every other widget keeps its default: shown and expanded.
    await expectCollapsed(page.getByTestId('dashboard-widget-schedule'), 'Schedule', false);
    await expectCollapsed(page.getByTestId('dashboard-widget-briefing'), 'Briefing', false);
    await page.getByRole('button', { name: 'Configure dashboard widgets' }).click();
    await expect(page.getByRole('checkbox', { name: /^Topic Focus/ })).not.toBeChecked();
    await expect(page.getByRole('checkbox', { name: /^Active Tasks/ })).toBeChecked();

    // Undo both and check the defaults are what survives the next reload.
    saved = widgetSaved(page);
    await page.getByRole('checkbox', { name: /^Topic Focus/ }).click();
    expect((await saved).ok()).toBeTruthy();
    saved = widgetSaved(page);
    await page.getByTestId('dashboard-widget-tasks').getByRole('button', { name: 'Expand Active Tasks' }).click();
    expect((await saved).ok()).toBeTruthy();

    await page.reload();
    await expect(page.getByTestId('dashboard-widget-topic_focus')).toBeVisible();
    await expectCollapsed(page.getByTestId('dashboard-widget-tasks'), 'Active Tasks', false);
    await expect(page.getByTestId('dashboard-widget-tasks').getByPlaceholder('Add a local task')).toBeVisible();
  });

  test('a hidden widget remembers it was collapsed when it is shown again', async ({ page }) => {
    await page.goto('/');
    const schedule = page.getByTestId('dashboard-widget-schedule');
    let saved = widgetSaved(page);
    await schedule.getByRole('button', { name: 'Collapse Schedule' }).click();
    await saved;

    await page.getByRole('button', { name: 'Configure dashboard widgets' }).click();
    saved = widgetSaved(page);
    await page.getByRole('checkbox', { name: /^Schedule/ }).click();
    await saved;
    await expect(schedule).toHaveCount(0);

    await page.reload();
    await page.getByRole('button', { name: 'Configure dashboard widgets' }).click();
    saved = widgetSaved(page);
    await page.getByRole('checkbox', { name: /^Schedule/ }).click();
    await saved;
    await expectCollapsed(page.getByTestId('dashboard-widget-schedule'), 'Schedule', true);
  });

  test('hiding every widget shows the empty state, also after a reload', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Configure dashboard widgets' }).click();
    for (const name of ['Briefing', 'Topic Focus', 'Important Emails', 'WhatsApp', 'Schedule', 'Active Tasks']) {
      const toggle = page.getByRole('checkbox', { name: new RegExp(`^${name}`) });
      const saved = widgetSaved(page);
      await toggle.click();
      await saved;
      await expect(toggle).not.toBeChecked();
    }
    await expect(page.getByText('No dashboard widgets selected')).toBeVisible();

    await page.reload();
    await expect(page.getByText('No dashboard widgets selected')).toBeVisible();
    await expect(page.locator('[data-testid^="dashboard-widget-"]')).toHaveCount(0);
    // The planner suggestions card is not a widget and stays.
    await expect(page.getByRole('heading', { name: 'Planner Suggestions' })).toBeVisible();
  });

  // Regression: before the saved preferences arrived, the dashboard rendered the defaults, so a
  // hidden widget flashed in and a toggle acted on the default state, inverting the saved choice.
  test('nothing acts on the default preferences while the saved ones are loading', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Configure dashboard widgets' }).click();
    const saved = widgetSaved(page);
    await page.getByRole('checkbox', { name: /^Schedule/ }).click();
    await saved;

    const held = deferred();
    await page.route('**/api/dashboard/widgets', async (route) => {
      if (route.request().method() === 'GET') await held.promise;
      await route.continue();
    });
    await page.reload();
    await expect(page.getByText('Loading dashboard widgets…')).toBeVisible();
    await expect(page.getByTestId('dashboard-widget-schedule')).toHaveCount(0);
    await page.getByRole('button', { name: 'Configure dashboard widgets' }).click();
    await expect(page.getByText('Loading your widget settings…')).toBeVisible();
    await expect(page.getByRole('checkbox', { name: /^Schedule/ })).toBeDisabled();

    held.release();
    const toggle = page.getByRole('checkbox', { name: /^Schedule/ });
    await expect(toggle).toBeEnabled();
    await expect(toggle).not.toBeChecked();
    await expect(page.getByTestId('dashboard-widget-schedule')).toHaveCount(0);
    await expect(page.getByTestId('dashboard-widget-tasks')).toBeVisible();
    await page.unroute('**/api/dashboard/widgets');
  });

  test('a save the backend rejects rolls the widget back', async ({ page }) => {
    await page.goto('/');
    await page.route('**/api/dashboard/widgets', (route) =>
      route.request().method() === 'PATCH'
        ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' })
        : route.continue(),
    );
    const tasks = page.getByTestId('dashboard-widget-tasks');
    const rejected = widgetSaved(page);
    await tasks.getByRole('button', { name: 'Collapse Active Tasks' }).click();
    expect((await rejected).status()).toBe(500);
    await expectCollapsed(tasks, 'Active Tasks', false);

    await page.unroute('**/api/dashboard/widgets');
    await page.reload();
    await expectCollapsed(page.getByTestId('dashboard-widget-tasks'), 'Active Tasks', false);
    expect((await savedWidgets(page)).tasks).toMatchObject({ is_collapsed: false });
  });

  test("one user's widget preferences do not change another user's dashboard", async ({ page, browser, baseURL }) => {
    // Two users, two sign-ins and three reloads: more than the default budget on a busy machine.
    test.slow();
    await page.goto('/');
    await page.getByRole('button', { name: 'Configure dashboard widgets' }).click();
    const saved = widgetSaved(page);
    await page.getByRole('checkbox', { name: /^Topic Focus/ }).click();
    await saved;

    const context = await browser.newContext({ baseURL, storageState: { cookies: [], origins: [] } });
    try {
      const userPage = await context.newPage();
      await signInAs(userPage, otherUser);
      await userPage.goto('/');

      // The other user starts from the defaults, not from the first user's choices.
      await expect(userPage.getByTestId('dashboard-widget-topic_focus')).toBeVisible();
      const userTasks = userPage.getByTestId('dashboard-widget-tasks');
      const userSaved = widgetSaved(userPage);
      await userTasks.getByRole('button', { name: 'Collapse Active Tasks' }).click();
      expect((await userSaved).ok()).toBeTruthy();
      await userPage.reload();
      await expectCollapsed(userPage.getByTestId('dashboard-widget-tasks'), 'Active Tasks', true);
      await expect(userPage.getByTestId('dashboard-widget-topic_focus')).toBeVisible();
    } finally {
      await context.close();
    }

    // ... and the first user still has their own: Topic Focus hidden, Active Tasks expanded.
    await page.reload();
    await expect(page.getByTestId('dashboard-widget-topic_focus')).toHaveCount(0);
    await expectCollapsed(page.getByTestId('dashboard-widget-tasks'), 'Active Tasks', false);
  });
});
