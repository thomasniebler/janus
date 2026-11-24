import { expect, test } from './fixtures';

// JAN-249: every tool call of a chat reply stays visible as a chip (status, name,
// duration) that expands inline to its redacted arguments and plain-text result.
// The e2e account has no LLM provider, so the live turn replays the exact frames
// /ws/chat sends (via page.routeWebSocket); the reload is served the stored
// `tool_calls` rows the backend returns for that conversation.

const PLACEHOLDER = 'Ask a question...';
const CONVERSATION_ID = 424243;

const searchCall = {
  call_id: 'run-1',
  tool_id: 'mail',
  tool_name: 'search_emails',
  args: { query: 'invoice', api_key: '[redacted]' },
  args_truncated: false,
  result_truncated: false,
};
const blockedCall = {
  call_id: 'run-2',
  tool_id: 'mail.send',
  tool_name: 'send_email',
  phase: 'blocked',
  args: { to: 'billing@example.com' },
  args_truncated: false,
  result_preview: 'BLOCKED: send_email was not run because the integration it needs is not connected.',
  result_truncated: false,
  duration_ms: 2,
};
const finishedSearch = {
  ...searchCall,
  phase: 'end',
  result_preview: '<b>2</b> invoices **found**',
  duration_ms: 380,
};
const REPLY = 'Two invoices arrived; I could not reply because sending mail is not connected.';

test('chat replies keep inspectable tool calls, live and after a reload', async ({ page }) => {
  const chatSocketUrls: string[] = [];
  await page.routeWebSocket(/\/ws\/chat/, (ws) => {
    chatSocketUrls.push(ws.url());
    ws.onMessage((raw) => {
      const text = typeof raw === 'string' ? raw : raw.toString();
      if (!text.includes('invoices')) return;
      ws.send(`__CONVERSATION_ID__:${CONVERSATION_ID}`);
      ws.send('__TOOL_SELECTION__:' + JSON.stringify({ task_id: 't', step_id: 's', tools: [], denied: [] }));
      ws.send('__START__');
      ws.send('__TOOL_EVENT__:' + JSON.stringify({ ...searchCall, phase: 'start', result_preview: null, duration_ms: null }));
      ws.send('__TOOL_CALL__:search_emails');
      ws.send('__TOOL_EVENT__:' + JSON.stringify(finishedSearch));
      ws.send('__TOOL_RESULT__:search_emails');
      ws.send('__TOOL_EVENT__:' + JSON.stringify(blockedCall));
      ws.send(REPLY);
      ws.send('__END__');
    });
  });

  await page.goto('/');
  const sidebar = page.getByRole('complementary', { name: 'Chat with Janus' });
  const input = sidebar.getByPlaceholder(PLACEHOLDER);
  await expect(input).toBeVisible();
  await input.fill('Any new invoices?');
  await input.press('Enter');

  await expect(sidebar.getByText(REPLY)).toBeVisible();
  expect(chatSocketUrls.some((url) => url.includes('tool_events=1'))).toBe(true);
  await expect(sidebar.getByText(/__TOOL_/)).toHaveCount(0);

  const searchChip = sidebar.getByRole('button', { name: 'search_emails, done, 380 ms' });
  await expect(searchChip).toBeVisible();
  await expect(sidebar.getByRole('button', { name: 'send_email, blocked, 2 ms' })).toBeVisible();

  await searchChip.click();
  await expect(searchChip).toHaveAttribute('aria-expanded', 'true');
  const details = sidebar.getByRole('region', { name: 'Tool call search_emails' });
  await expect(details).toBeVisible();
  // Untrusted output is plain text: the markup is shown, never rendered.
  await expect(details.getByText('<b>2</b> invoices **found**')).toBeVisible();
  await expect(details.locator('b')).toHaveCount(0);
  await expect(details.getByText(/"api_key": "\[redacted\]"/)).toBeVisible();

  // After a reload the same chips come from the stored tool_calls rows.
  await page.route(`**/api/conversations/${CONVERSATION_ID}`, (route) =>
    route.fulfill({
      json: {
        id: CONVERSATION_ID,
        title: 'Invoices',
        created_at: '2026-10-03T08:00:00Z',
        updated_at: '2026-10-03T08:01:00Z',
        messages: [
          { id: 1, role: 'user', content: 'Any new invoices?', created_at: '2026-10-03T08:00:00Z', tool_calls: [] },
          {
            id: 2,
            role: 'assistant',
            content: REPLY,
            created_at: '2026-10-03T08:00:05Z',
            tool_calls: [finishedSearch, blockedCall],
          },
        ],
      },
    })
  );
  await page.reload();

  await expect(sidebar.getByText(REPLY)).toBeVisible();
  await expect(sidebar.getByRole('button', { name: 'search_emails, done, 380 ms' })).toBeVisible();
  await sidebar.getByRole('button', { name: 'send_email, blocked, 2 ms' }).click();
  await expect(sidebar.getByRole('region', { name: 'Tool call send_email' })).toContainText('BLOCKED: send_email was not run');
});
