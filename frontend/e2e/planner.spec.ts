import type { Page } from '@playwright/test';
import { lastJsonLine, runBackendPython } from './backendSeed';
import { expect, test } from './fixtures';
import {
  E2E_BASE_URL,
  E2E_FAKE_UPSTREAM_PORT,
  E2E_FAKE_UPSTREAM_URL,
  E2E_GOOGLE_CLIENT_ID,
  E2E_GOOGLE_CLIENT_SECRET,
} from './env';
import { startFakeUpstream, type FakeHandler, type FakeUpstream, type RecordedRequest } from './fake-upstream';
import { answerConfirm, api } from './helpers';

/**
 * Planner (JAN-221): Google Calendar/Tasks connected through the real OAuth callback,
 * a Jira site added through the API, the suggest-then-confirm review queue, and local
 * planner items on the Dashboard. Google and Jira are a local fake (`fake-upstream.ts`):
 * the browser's consent redirect to accounts.google.com is answered by `page.route`, and
 * the backend's own token, userinfo, Calendar, Tasks and Jira calls reach the fake
 * through JANUS_GOOGLE_API_BASE_URL and the Jira account's site URL. Nothing calls a
 * real Google or Atlassian endpoint.
 */

// Datetime inputs and the "today" schedule widget then agree on one day.
test.use({ timezoneId: 'UTC' });
// Each Python step (sync, seed) starts its own `uv run`, which takes a few seconds.
test.describe.configure({ mode: 'serial', timeout: 120_000 });

// The 30-minute background sync would do this on its own; the spec runs the same
// PlannerService.sync_all once instead of waiting.
const SYNC_SCRIPT = `
import json
from janus import models, planner_service

service = planner_service.PlannerService()
try:
    service.sync_all()
    accounts = service.db.query(models.PlannerAccount).all()
    print(json.dumps({a.name: a.last_sync_error for a in accounts}))
finally:
    service.db.close()
`;

// The planner summary the Morning briefing hands the model (builtin_triggers._prepare_briefing).
const BRIEFING_SUMMARY_SCRIPT = `
import json
from janus import planner_service

payload = planner_service.PlannerService(db).get_briefing_payload(user_id=user.id)
print(json.dumps({"summary": str(payload["summary"])}))
`;

// Suggestions as the mail extractor records them (LocalPlannerService.record_email_suggestions
// minus the model call): normalized, then upserted as pending.
const SEED_SUGGESTIONS_SCRIPT = `
import json
from datetime import UTC, datetime, timedelta
# planner_service first: importing local_planner_service on its own hits an import cycle.
from janus import planner_service  # noqa: F401
from janus.services.local_planner_service import LocalPlannerService

stamp = args[0]
service = LocalPlannerService(db)
start = datetime.now(UTC).replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
items = [
    {"suggestion_type": "task", "title": f"E2E suggested task {stamp}", "description": "Reply to the vendor.",
     "confidence": "high", "reason": "The email asks for a reply by Friday.",
     "due_at": (start + timedelta(days=2)).isoformat()},
    {"suggestion_type": "event", "title": f"E2E suggested event {stamp}", "confidence": "medium",
     "reason": "The email proposes a call.", "start_time": start.isoformat(),
     "end_time": (start + timedelta(hours=1)).isoformat()},
]
rows = [
    service._upsert_suggestion(
        service._normalize_suggestion_payload(
            user_id=user.id, source=models.PlannerItemSource.EMAIL_AGENT, suggestion=item
        )
    )
    for item in items
]
db.commit()
print(json.dumps([row.id for row in rows]))
`;

// Jira accounts have no delete route (there is no Jira UI yet), so the spec removes its own.
const DELETE_PLANNER_ACCOUNT_SCRIPT = `
import json

ids = [a.id for a in db.query(models.PlannerAccount).filter(models.PlannerAccount.name == args[0]).all()]
db.query(models.TaskRecord).filter(models.TaskRecord.planner_account_id.in_(ids)).delete(synchronize_session=False)
db.query(models.CalendarEvent).filter(models.CalendarEvent.planner_account_id.in_(ids)).delete(synchronize_session=False)
db.query(models.PlannerAccount).filter(models.PlannerAccount.id.in_(ids)).delete(synchronize_session=False)
db.commit()
print(json.dumps(ids))
`;

const todayUtc = () => new Date().toISOString().slice(0, 10);

const CLIENT_ID = E2E_GOOGLE_CLIENT_ID;
const CLIENT_SECRET = E2E_GOOGLE_CLIENT_SECRET;
const ACCESS_TOKEN = 'e2e-google-access-token-never-returned';
const REFRESH_TOKEN = 'e2e-google-refresh-token-never-returned';
const PRIMARY_CALENDAR = 'e2e-user@example.com';
const TEAM_CALENDAR = 'e2e-team@group.calendar.google.com';
const GRANTED_SCOPES = [
  'openid',
  'https://www.googleapis.com/auth/userinfo.email',
  'https://www.googleapis.com/auth/calendar.readonly',
  'https://www.googleapis.com/auth/tasks.readonly',
].join(' ');

const JIRA_ACCOUNT = 'E2E Jira';
const JIRA_TOKEN = 'e2e-jira@example.com:e2e-jira-api-token-never-returned';

/** The fake Google (token, userinfo, Calendar v3, Tasks v1) and Jira Cloud (enhanced JQL search). */
const fakeGoogleAndJira: FakeHandler = (request: RecordedRequest) => {
  const url = new URL(request.url, E2E_FAKE_UPSTREAM_URL);
  const route = `${request.method} ${decodeURIComponent(url.pathname)}`;
  const day = todayUtc();
  switch (route) {
    case 'POST /google/token': {
      const form = new URLSearchParams(request.body);
      if (form.get('code') === 'e2e-rejected-code') {
        return { status: 400, json: { error: 'invalid_grant', error_description: 'Bad Request' } };
      }
      return {
        json: {
          access_token: ACCESS_TOKEN,
          refresh_token: REFRESH_TOKEN,
          expires_in: 3599,
          token_type: 'Bearer',
          scope: GRANTED_SCOPES,
        },
      };
    }
    case 'GET /google/oauth2/v3/userinfo':
      return { json: { sub: 'e2e-google-subject', email: PRIMARY_CALENDAR } };
    case 'GET /google/calendar/v3/users/me/calendarList':
      return {
        json: {
          items: [
            { id: PRIMARY_CALENDAR, summary: 'E2E Personal', primary: true, accessRole: 'owner' },
            { id: TEAM_CALENDAR, summary: 'E2E Team', accessRole: 'reader' },
          ],
        },
      };
    case `GET /google/calendar/v3/calendars/primary/events`:
    case `GET /google/calendar/v3/calendars/${PRIMARY_CALENDAR}/events`:
      return {
        json: {
          items: [
            {
              id: 'standup',
              summary: 'E2E Google standup',
              location: 'Room 4',
              start: { dateTime: `${day}T12:00:00Z` },
              end: { dateTime: `${day}T12:30:00Z` },
            },
          ],
        },
      };
    case `GET /google/calendar/v3/calendars/${TEAM_CALENDAR}/events`:
      return {
        json: {
          items: [
            {
              id: 'review',
              summary: 'E2E Team review',
              start: { dateTime: `${day}T13:00:00Z` },
              end: { dateTime: `${day}T14:00:00Z` },
            },
          ],
        },
      };
    case 'GET /google/tasks/v1/users/@me/lists':
      return { json: { items: [{ id: 'e2e-list', title: 'E2E list' }] } };
    case 'GET /google/tasks/v1/lists/e2e-list/tasks':
      return { json: { items: [{ id: 'gtask-1', title: 'E2E Google task', status: 'needsAction' }] } };
    case 'GET /jira/rest/api/3/search/jql':
      return {
        json: {
          issues: [
            {
              key: 'E2E-101',
              fields: { summary: 'E2E Jira: write release notes', status: { name: 'To Do', statusCategory: { key: 'new' } } },
            },
            {
              key: 'E2E-102',
              fields: {
                summary: 'E2E Jira: review the parser change',
                status: { name: 'In Review', statusCategory: { key: 'indeterminate' } },
              },
            },
          ],
          isLast: true,
        },
      };
    default:
      return undefined;
  }
};

/**
 * Answers the browser's trip to Google's consent screen: checks the authorization request
 * the backend built, then redirects straight back to its callback as Google would after
 * the user clicks Allow, with `code` as the authorization code. Returns the state it saw.
 */
const mockGoogleConsent = async (page: Page, code: string) => {
  const seen: { state?: string } = {};
  await page.route('https://accounts.google.com/**', async (route) => {
    const authorize = new URL(route.request().url());
    expect(authorize.searchParams.get('client_id')).toBe(CLIENT_ID);
    expect(authorize.searchParams.get('redirect_uri')).toBe(`${E2E_BASE_URL}/api/connections/google/callback`);
    expect(authorize.searchParams.get('code_challenge_method')).toBe('S256');
    expect(authorize.searchParams.get('code_challenge')).toBeTruthy();
    expect(authorize.searchParams.get('scope')).toContain('https://www.googleapis.com/auth/calendar.readonly');
    expect(authorize.searchParams.get('access_type')).toBe('offline');
    seen.state = authorize.searchParams.get('state') ?? undefined;
    const callback = new URL(authorize.searchParams.get('redirect_uri')!);
    callback.searchParams.set('code', code);
    callback.searchParams.set('state', seen.state ?? '');
    callback.searchParams.set('scope', GRANTED_SCOPES);
    await route.fulfill({ status: 302, headers: { location: callback.toString() } });
  });
  return seen;
};

/** Leaves Google disconnected, unlinked, and as other specs expect. */
const resetGoogle = async (page: Page) => {
  const { csrf_token: csrfToken } = await (await page.request.get('/api/auth/csrf')).json();
  const headers = { 'X-CSRF-Token': csrfToken };
  // Either may already be gone; only the final state matters.
  await page.request.delete('/api/connections/google', { headers });
  await page.request.delete('/api/auth/google/link', { headers });
};

let upstream: FakeUpstream;

test.beforeAll(async () => {
  upstream = await startFakeUpstream(E2E_FAKE_UPSTREAM_PORT, fakeGoogleAndJira);
});

test.afterAll(async () => {
  await upstream?.close();
});

test.describe('Planner: Google Calendar and Tasks', () => {
  test.afterEach(async ({ page }) => {
    await resetGoogle(page);
  });

  test('connects Google through the OAuth callback, syncs the chosen calendars and tasks, and disconnects', async ({
    page,
  }) => {
    upstream.requests.length = 0;
    const consent = await mockGoogleConsent(page, 'e2e-auth-code');
    await page.goto('/settings?tab=connections');
    // The Connections tab renders once the connection status loads, which can exceed the
    // default 5s assertion timeout while other specs share the backend. The client id and
    // secret come from the configuration (see env.ts), so Connect is enabled right away.
    await expect(page.getByRole('button', { name: 'Connect Google' })).toBeEnabled({ timeout: 15_000 });
    await page.getByRole('button', { name: 'Connect Google' }).click();

    await expect(page.getByText('Google account connected successfully!')).toBeVisible();
    await expect.poll(() => new URL(page.url()).searchParams.has('connected')).toBe(false);
    await expect(page.getByText(`Connected as ${PRIMARY_CALENDAR}`)).toBeVisible();
    await expect(page.getByText('Sign in with Google: linked')).toBeVisible();

    // The backend exchanged the code with the PKCE verifier and the stored client secret.
    const exchange = upstream.requests.find((request) => request.url === '/google/token');
    expect(exchange, 'token exchange reached the fake Google').toBeTruthy();
    const form = new URLSearchParams(exchange!.body);
    expect(form.get('grant_type')).toBe('authorization_code');
    expect(form.get('code')).toBe('e2e-auth-code');
    expect(form.get('code_verifier')).toBeTruthy();
    expect(form.get('redirect_uri')).toBe(`${E2E_BASE_URL}/api/connections/google/callback`);
    // The decrypted client secret authenticates the exchange (HTTP Basic, RFC 6749 2.3.1).
    expect(exchange!.headers.authorization).toBe(
      `Basic ${Buffer.from(`${CLIENT_ID}:${CLIENT_SECRET}`).toString('base64')}`
    );
    const userinfo = upstream.requests.find((request) => request.url === '/google/oauth2/v3/userinfo');
    expect(userinfo?.headers.authorization).toBe(`Bearer ${ACCESS_TOKEN}`);

    // The single-use state cannot be replayed.
    await page.goto(`/api/connections/google/callback?code=e2e-auth-code&state=${consent.state}`);
    await expect(page.getByText('OAuth2 state token expired or invalid. Please try again.')).toBeVisible();

    // Secrets stay server-side: neither the settings nor the accounts answer carries them.
    const settingsBody = await (await page.request.get('/api/planner/settings')).text();
    const accountsResponse = await page.request.get('/api/planner/accounts');
    const accountsBody = await accountsResponse.text();
    for (const secret of [CLIENT_SECRET, ACCESS_TOKEN, REFRESH_TOKEN]) {
      expect(settingsBody).not.toContain(secret);
      expect(await (await page.request.get('/api/connections')).text()).not.toContain(secret);
      expect(accountsBody).not.toContain(secret);
    }
    const accounts: Array<Record<string, unknown>> = await accountsResponse.json();
    expect(accounts.map((account) => account.provider).sort()).toEqual(['google_calendar', 'google_tasks']);
    for (const account of accounts) expect(account).not.toHaveProperty('token');

    // The calendars are chosen on the Planner page. The primary one, synced by default, shows
    // as selected; the shared one does not.
    await page.goto('/planner');
    const personal = page.getByRole('checkbox', { name: /E2E Personal/ });
    const team = page.getByRole('checkbox', { name: /E2E Team/ });
    await expect(personal).toBeChecked();
    await expect(team).not.toBeChecked();
    await expect(page.getByText('Shared (reader)')).toBeVisible();
    await team.check();
    await page.getByRole('button', { name: 'Save selection' }).click();
    await expect(page.getByText('Calendar selection saved.')).toBeVisible();
    await page.reload();
    await expect(page.getByRole('checkbox', { name: /E2E Team/ })).toBeChecked();
    await expect(page.getByRole('checkbox', { name: /E2E Personal/ })).toBeChecked();

    const syncResult = lastJsonLine<Record<string, string | null>>(await runBackendPython(SYNC_SCRIPT));
    expect(syncResult['Google Calendar']).toBeNull();
    expect(syncResult['Google Tasks']).toBeNull();

    await page.goto('/');
    const schedule = page.getByTestId('dashboard-widget-schedule');
    for (const title of ['E2E Google standup', 'E2E Team review']) {
      const item = schedule.locator('[data-ui-item]').filter({ hasText: title });
      await expect(item).toBeVisible();
      await expect(item).toContainText('Google Calendar');
    }
    await expect(schedule.getByText('Room 4')).toBeVisible();
    const tasks = page.getByTestId('dashboard-widget-tasks');
    const googleTask = tasks.getByRole('listitem').filter({ hasText: 'E2E Google task' });
    await expect(googleTask).toContainText('Google Tasks');
    // Imported tasks are read-only: no status control.
    await expect(tasks.getByRole('combobox', { name: 'Status of E2E Google task' })).toHaveCount(0);

    const briefing = lastJsonLine<{ summary: string }>(await runBackendPython(BRIEFING_SUMMARY_SCRIPT));
    expect(briefing.summary).toContain('E2E Google standup');
    expect(briefing.summary).toContain('E2E Google task');

    // Disconnecting removes the accounts and everything they imported.
    await page.goto('/settings?tab=connections');
    await page.getByRole('button', { name: 'Disconnect Google' }).click();
    await expect(page.getByText('Google disconnected.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Connect Google' })).toBeVisible();
    await page.goto('/');
    await expect(page.getByTestId('dashboard-widget-tasks')).toBeVisible();
    await expect(page.getByText('E2E Google standup')).toHaveCount(0);
    await expect(page.getByText('E2E Google task')).toHaveCount(0);
  });

  test('a rejected authorization code leaves Google unconnected and explains why', async ({ page }) => {
    await mockGoogleConsent(page, 'e2e-rejected-code');
    await page.goto('/settings?tab=connections');
    await expect(page.getByRole('button', { name: 'Connect Google' })).toBeEnabled();

    await page.getByRole('button', { name: 'Connect Google' }).click();

    await expect(
      page.getByText('Google rejected the authorization code (expired or already used). Please connect again.')
    ).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Connections', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('button', { name: 'Connect Google' })).toBeEnabled();
    await expect(page.getByText(/Connected as/)).toHaveCount(0);
    const accounts: Array<{ provider: string }> = await (await page.request.get('/api/planner/accounts')).json();
    expect(accounts.filter((account) => account.provider.startsWith('google'))).toEqual([]);
  });

  test('consent denied at Google returns to the Connections tab with its message', async ({ page }) => {
    await page.route('https://accounts.google.com/**', async (route) => {
      const authorize = new URL(route.request().url());
      const callback = new URL(authorize.searchParams.get('redirect_uri')!);
      callback.searchParams.set('error', 'access_denied');
      callback.searchParams.set('state', authorize.searchParams.get('state') ?? '');
      await route.fulfill({ status: 302, headers: { location: callback.toString() } });
    });
    upstream.requests.length = 0;
    await page.goto('/settings?tab=connections');

    await page.getByRole('button', { name: 'Connect Google' }).click();

    await expect(page.getByText('Google authorization was denied.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Connect Google' })).toBeVisible();
    // Nothing was exchanged with Google.
    expect(upstream.requests.filter((request) => request.url.startsWith('/google/token'))).toEqual([]);
  });
});

test.describe('Planner: Jira', () => {
  test.afterEach(async () => {
    await runBackendPython(DELETE_PLANNER_ACCOUNT_SCRIPT, [JIRA_ACCOUNT]);
  });

  test('a Jira site added through the API syncs its open issues into the dashboard and the briefing', async ({
    page,
  }) => {
    upstream.requests.length = 0;
    // There is deliberately no Jira UI yet: `janus planner add` posts this same request.
    const created = await api(page, 'post', '/api/planner/accounts', {
      name: JIRA_ACCOUNT,
      provider: 'jira',
      token: JIRA_TOKEN,
      api_url: `${E2E_FAKE_UPSTREAM_URL}/jira`,
    });
    expect(await created.text()).not.toContain('never-returned');
    expect(await created.json()).not.toHaveProperty('token');
    expect(await (await page.request.get('/api/planner/accounts')).text()).not.toContain('never-returned');

    const syncResult = lastJsonLine<Record<string, string | null>>(await runBackendPython(SYNC_SCRIPT));
    expect(syncResult[JIRA_ACCOUNT]).toBeNull();

    // The stored token was decrypted for the call and used as Jira Cloud basic auth.
    const search = upstream.requests.find((request) => request.url.startsWith('/jira/rest/api/3/search/jql'));
    expect(search, 'the Jira search reached the fake site').toBeTruthy();
    expect(new URL(search!.url, E2E_FAKE_UPSTREAM_URL).searchParams.get('jql')).toBe(
      'assignee = currentUser() AND statusCategory != Done'
    );
    expect(search!.headers.authorization).toBe(`Basic ${Buffer.from(JIRA_TOKEN).toString('base64')}`);

    // Jira's own status names map onto Janus statuses by status category.
    const tasks: Array<{ title: string; status: string; read_only: boolean; source_label: string }> = await (
      await page.request.get('/api/planner/tasks')
    ).json();
    const jiraTasks = tasks.filter((task) => task.source_label === JIRA_ACCOUNT);
    expect(jiraTasks.map(({ title, status, read_only }) => ({ title, status, read_only })).sort((a, b) =>
      a.title.localeCompare(b.title)
    )).toEqual([
      { title: 'E2E Jira: review the parser change', status: 'In Progress', read_only: true },
      { title: 'E2E Jira: write release notes', status: 'Todo', read_only: true },
    ]);

    // A new account starts empty: it inherits nothing an earlier, disconnected account
    // imported (the Google test above frees the planner account ids this one may reuse).
    const events: Array<{ title: string; source_label: string }> = await (
      await page.request.get('/api/planner/events')
    ).json();
    expect(events.filter((event) => event.source_label === JIRA_ACCOUNT)).toEqual([]);

    await page.goto('/');
    const widget = page.getByTestId('dashboard-widget-tasks');
    for (const title of ['E2E Jira: write release notes', 'E2E Jira: review the parser change']) {
      await expect(widget.getByRole('listitem').filter({ hasText: title })).toContainText(JIRA_ACCOUNT);
    }

    const briefing = lastJsonLine<{ summary: string }>(await runBackendPython(BRIEFING_SUMMARY_SCRIPT));
    expect(briefing.summary).toContain('E2E Jira: write release notes');
    expect(briefing.summary).toContain('E2E Jira: review the parser change');
  });
});

// Suggestions and local items are per user, so these run as throwaway users (JAN-336): the
// review queue and the Dashboard show only what the test itself created.
test.describe('Planner: suggestion review', () => {
  test.use({ asThrowawayUser: true });

  test('a suggestion becomes a planner item only once approved; a dismissed one never does', async ({
    page,
    account,
  }) => {
    const stamp = `${Date.now()}`;
    const taskTitle = `E2E suggested task ${stamp}`;
    const editedTitle = `E2E approved task ${stamp}`;
    const eventTitle = `E2E suggested event ${stamp}`;
    const [taskSuggestionId, eventSuggestionId] = lastJsonLine<number[]>(
      await runBackendPython(SEED_SUGGESTIONS_SCRIPT, [stamp], { as: account.username }),
    );

    await page.goto('/');
    await expect(page.getByText('2 Pending')).toBeVisible();
    const taskCard = page.getByTestId(`planner-suggestion-${taskSuggestionId}`);
    const eventCard = page.getByTestId(`planner-suggestion-${eventSuggestionId}`);
    await expect(taskCard.getByLabel('Suggestion title')).toHaveValue(taskTitle);
    await expect(taskCard).toContainText('The email asks for a reply by Friday.');
    await expect(taskCard.getByLabel('Suggestion due')).not.toHaveValue('');
    await expect(eventCard.getByLabel('Suggestion title')).toHaveValue(eventTitle);
    await expect(eventCard.getByLabel('Suggestion start')).not.toHaveValue('');

    // Suggest-then-confirm: a pending suggestion is not a planner item yet.
    const localTitles = async (kind: 'tasks' | 'events') =>
      ((await (await page.request.get(`/api/planner/local/${kind}`)).json()) as Array<{ title: string }>).map(
        (item) => item.title
      );
    expect(await localTitles('tasks')).not.toContain(taskTitle);
    expect(await localTitles('events')).not.toContain(eventTitle);

    // Edit before approving: the approved item carries the edit.
    await taskCard.getByLabel('Suggestion title').fill(editedTitle);
    await taskCard.getByRole('button', { name: 'Approve' }).click();
    await expect(taskCard).toHaveCount(0);
    await expect(page.getByText('1 Pending')).toBeVisible();
    const approvedTask = page.getByTestId('dashboard-widget-tasks').getByRole('listitem').filter({ hasText: editedTitle });
    await expect(approvedTask).toContainText('Email Agent', { timeout: 15_000 });

    await eventCard.getByRole('button', { name: 'Dismiss' }).click();
    await expect(eventCard).toHaveCount(0);
    await expect(page.getByText('No pending planner suggestions.')).toBeVisible();

    const reviewed: Array<{ id: number; status: string; target_task_id: number | null; target_event_id: number | null }> =
      await (await page.request.get('/api/planner/suggestions?include_applied=true')).json();
    const approved = reviewed.find((suggestion) => suggestion.id === taskSuggestionId)!;
    const dismissed = reviewed.find((suggestion) => suggestion.id === eventSuggestionId)!;
    expect(approved.status).toBe('applied');
    expect(approved.target_task_id).not.toBeNull();
    expect(dismissed.status).toBe('dismissed');
    expect(dismissed.target_event_id).toBeNull();
    expect(await localTitles('tasks')).toContain(editedTitle);
    expect(await localTitles('tasks')).not.toContain(taskTitle);
    expect(await localTitles('events')).not.toContain(eventTitle);

    // Reviewed suggestions stay reviewed across a reload.
    await page.reload();
    await expect(page.getByText('No pending planner suggestions.')).toBeVisible();

    // Leave the shared admin's task list as it was, so later specs' empty states still hold.
    await api(page, 'delete', `/api/planner/local/tasks/${approved.target_task_id}`);
    expect(await localTitles('tasks')).not.toContain(editedTitle);
  });
});

test.describe('Planner: local items on the Dashboard', () => {
  test.use({ asThrowawayUser: true });

  test('creates a local event and a task with a due date, edits them, completes the task, and deletes items', async ({
    page,
  }) => {
    const stamp = Date.now();
    const eventTitle = `E2E local event ${stamp}`;
    const renamedEvent = `E2E moved event ${stamp}`;
    const taskTitle = `E2E local task ${stamp}`;
    const day = todayUtc();

    await page.goto('/');
    const schedule = page.getByTestId('dashboard-widget-schedule');
    await schedule.getByPlaceholder('Add a local event').fill(eventTitle);
    await schedule.getByLabel('Event start').fill(`${day}T15:00`);
    await schedule.getByLabel('Event end').fill(`${day}T15:45`);
    const createdEvent = page.waitForResponse(
      (response) => response.url().endsWith('/api/planner/local/events') && response.request().method() === 'POST'
    );
    await schedule.getByRole('button', { name: 'Add event' }).click();
    const event = await (await createdEvent).json();
    expect(event.start_time).toContain(`${day}T15:00`);
    const eventRow = schedule.locator('[data-ui-item]').filter({ hasText: eventTitle });
    await expect(eventRow).toContainText('Manual', { timeout: 15_000 });
    await expect(eventRow).toContainText('15:00');

    // The Dashboard has no event editor; an edit through the API shows up there.
    await api(page, 'patch', `/api/planner/local/events/${event.id}`, {
      title: renamedEvent,
      start_time: `${day}T16:00:00Z`,
      end_time: `${day}T16:30:00Z`,
    });
    await page.reload();
    const renamedRow = schedule.locator('[data-ui-item]').filter({ hasText: renamedEvent });
    await expect(renamedRow).toContainText('16:00');
    await expect(schedule.getByText(eventTitle)).toHaveCount(0);

    const tasks = page.getByTestId('dashboard-widget-tasks');
    await tasks.getByPlaceholder('Add a local task').fill(taskTitle);
    await tasks.getByLabel('Task due').fill('2031-03-14T09:30');
    await tasks.getByRole('button', { name: 'Add task' }).click();
    const taskRow = tasks.getByRole('listitem').filter({ hasText: taskTitle });
    await expect(taskRow).toContainText(`Due ${new Date('2031-03-14T09:30:00Z').toLocaleDateString('en-US')}`, {
      timeout: 15_000,
    });

    const status = tasks.getByRole('combobox', { name: `Status of ${taskTitle}` });
    await expect(status).toHaveValue('Todo');
    await status.selectOption('In Progress');
    await expect(status).toHaveValue('In Progress');
    await page.reload();
    await expect(tasks.getByRole('combobox', { name: `Status of ${taskTitle}` })).toHaveValue('In Progress');

    await tasks.getByRole('combobox', { name: `Status of ${taskTitle}` }).selectOption('Done');
    await expect(tasks.getByText(taskTitle)).toHaveCount(0, { timeout: 15_000 });
    const done: Array<{ title: string; status: string }> = await (
      await page.request.get('/api/planner/local/tasks?include_completed=true')
    ).json();
    expect(done.find((task) => task.title === taskTitle)?.status).toBe('Done');

    // Delete (JAN-221): Cancel keeps the event, Delete removes it, and it stays gone across a reload.
    await renamedRow.getByRole('button', { name: `Delete ${renamedEvent}` }).click();
    await answerConfirm(page, 'Delete event?', 'Cancel');
    await expect(renamedRow).toBeVisible();
    await renamedRow.getByRole('button', { name: `Delete ${renamedEvent}` }).click();
    await answerConfirm(page, 'Delete event?', 'Delete');
    await expect(schedule.getByText(renamedEvent)).toHaveCount(0);
    await page.reload();
    await expect(page.getByTestId('dashboard-widget-tasks')).toBeVisible();
    await expect(schedule.getByText(renamedEvent)).toHaveCount(0);
    const events: Array<{ id: number }> = await (await page.request.get('/api/planner/local/events')).json();
    expect(events.map((item) => item.id)).not.toContain(event.id);

    // Deleting a category keeps its subtask, which moves up to the root of the list.
    const parentTitle = `E2E category ${stamp}`;
    const childTitle = `E2E subtask ${stamp}`;
    const parent = await (await api(page, 'post', '/api/planner/local/tasks', { title: parentTitle })).json();
    await api(page, 'post', '/api/planner/local/tasks', { title: childTitle, parent_task_id: parent.id });
    await page.reload();
    const category = tasks.getByTestId(`dashboard-task-group-manual-${parent.id}`);
    await expect(category.getByRole('list', { name: `Subtasks of ${parentTitle}` })).toContainText(childTitle);
    await category.getByRole('button', { name: `Delete ${parentTitle}` }).click();
    await answerConfirm(page, 'Delete task?', 'Delete');
    await expect(category).toHaveCount(0);
    await expect(tasks.getByText(parentTitle)).toHaveCount(0);
    const promoted = tasks.getByRole('listitem').filter({ hasText: childTitle });
    await expect(promoted.getByRole('combobox', { name: `Status of ${childTitle}` })).toHaveValue('Todo');

    await promoted.getByRole('button', { name: `Delete ${childTitle}` }).click();
    await answerConfirm(page, 'Delete task?', 'Delete');
    await expect(tasks.getByText(childTitle)).toHaveCount(0);
    const remaining: Array<{ title: string }> = await (
      await page.request.get('/api/planner/local/tasks?include_completed=true')
    ).json();
    expect(remaining.map((task) => task.title)).not.toEqual(expect.arrayContaining([parentTitle]));
    expect(remaining.map((task) => task.title)).not.toEqual(expect.arrayContaining([childTitle]));
  });
});
