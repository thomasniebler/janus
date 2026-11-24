import { type Page, type WebSocketRoute } from '@playwright/test';
import { expect, test } from './fixtures';

// JAN-254: a confirmation-required tool call pauses on an inline approval card;
// Approve / Deny goes back over /ws/chat for that one call_id, and the card then
// collapses into the call's chip. The e2e account has no LLM provider, so the turn
// replays the frames /ws/chat sends (page.routeWebSocket) and answers like the
// backend does once the card is answered.

const PLACEHOLDER = 'Ask a question...';

const sendCall = {
  call_id: 'run-approve-1',
  tool_id: 'mail.send',
  tool_name: 'send_email',
  args: { to: 'boss@example.com', subject: '<b>Status</b>', api_key: '[redacted]' },
  args_truncated: false,
  result_truncated: false,
};
const approvalRequest = {
  call_id: sendCall.call_id,
  tool_id: 'mail.send',
  tool_name: 'send_email',
  display_name: 'Send mail',
  risk_level: 'high',
  reason: 'Send email from your connected accounts.',
  args: sendCall.args,
  args_truncated: false,
  timeout_seconds: 300,
};

const routeChat = async (page: Page, answers: Array<Record<string, unknown>>) => {
  await page.routeWebSocket(/\/ws\/chat/, (ws: WebSocketRoute) => {
    ws.onMessage((raw) => {
      const text = typeof raw === 'string' ? raw : raw.toString();
      const frame = JSON.parse(text) as Record<string, unknown>;
      if (frame.type === 'tool_approval') {
        answers.push(frame);
        const approved = frame.decision === 'approve' || frame.decision === 'always';
        ws.send(
          '__TOOL_EVENT__:' +
            JSON.stringify({
              ...sendCall,
              phase: approved ? 'end' : 'blocked',
              result_preview: approved
                ? 'Sent.'
                : 'BLOCKED: send_email was not run because the user declined. Nothing was changed.',
              duration_ms: 12,
              approval: approved ? 'approved' : 'denied',
            })
        );
        ws.send(approved ? 'I sent the status mail.' : 'Okay, I did not send it.');
        ws.send('__END__');
        return;
      }
      ws.send('__CONVERSATION_ID__:424254');
      ws.send(
        '__TOOL_SELECTION__:' +
          JSON.stringify({
            task_id: 't',
            step_id: 's',
            tools: [{ id: 'mail.send', confirmation: 'required' }],
            denied: [],
          })
      );
      ws.send('__START__');
      ws.send('__TOOL_EVENT__:' + JSON.stringify({ ...sendCall, phase: 'start', result_preview: null, duration_ms: null }));
      ws.send('__TOOL_APPROVAL__:' + JSON.stringify(approvalRequest));
    });
  });
};

test('approving the card runs that one call and collapses it into a chip', async ({ page }) => {
  const answers: Array<Record<string, unknown>> = [];
  await routeChat(page, answers);

  await page.goto('/');
  const sidebar = page.getByRole('complementary', { name: 'Chat with Janus' });
  const input = sidebar.getByPlaceholder(PLACEHOLDER);
  await input.fill('Mail my boss the status');
  await input.press('Enter');

  const card = sidebar.getByRole('group', { name: 'Janus wants to use Send mail' });
  await expect(card).toBeVisible();
  // Arguments are redacted and shown as text, never rendered.
  await expect(card.getByText(/"subject": "<b>Status<\/b>"/)).toBeVisible();
  await expect(card.getByText(/"api_key": "\[redacted\]"/)).toBeVisible();
  await expect(card.locator('b')).toHaveCount(0);

  await card.getByRole('button', { name: 'Approve' }).click();
  await expect(sidebar.getByText('I sent the status mail.')).toBeVisible();
  await expect(card).toHaveCount(0);
  await expect(sidebar.getByRole('button', { name: 'send_email, done, approved by you, 12 ms' })).toBeVisible();
  expect(answers).toEqual([{ type: 'tool_approval', call_id: 'run-approve-1', decision: 'approve' }]);
});

test('denying on the /chat page in another shell blocks the call', async ({ page }) => {
  const answers: Array<Record<string, unknown>> = [];
  await routeChat(page, answers);

  await page.goto('/chat?ui=v3');
  const chatPage = page.getByTestId('chat-page');
  const input = chatPage.getByPlaceholder(PLACEHOLDER);
  await input.fill('Mail my boss the status');
  await input.press('Enter');

  const card = chatPage.getByRole('group', { name: 'Janus wants to use Send mail' });
  await expect(card).toBeVisible();
  await card.getByRole('button', { name: 'Deny' }).click();

  await expect(chatPage.getByText('Okay, I did not send it.')).toBeVisible();
  await expect(card).toHaveCount(0);
  const chip = chatPage.getByRole('button', { name: 'send_email, blocked, denied by you, 12 ms' });
  await chip.click();
  await expect(chatPage.getByRole('region', { name: 'Tool call send_email' })).toContainText(
    'BLOCKED: send_email was not run because the user declined.'
  );
  expect(answers).toEqual([{ type: 'tool_approval', call_id: 'run-approve-1', decision: 'deny' }]);
});

test('Always allow approves the call and answers "always" for it (JAN-359)', async ({ page }) => {
  const answers: Array<Record<string, unknown>> = [];
  await routeChat(page, answers);

  await page.goto('/');
  const sidebar = page.getByRole('complementary', { name: 'Chat with Janus' });
  const input = sidebar.getByPlaceholder(PLACEHOLDER);
  await input.fill('Mail my boss the status');
  await input.press('Enter');

  const card = sidebar.getByRole('group', { name: 'Janus wants to use Send mail' });
  await card.getByRole('button', { name: 'Always allow' }).click();
  await expect(sidebar.getByText('I sent the status mail.')).toBeVisible();
  await expect(card).toHaveCount(0);
  expect(answers).toEqual([{ type: 'tool_approval', call_id: 'run-approve-1', decision: 'always' }]);
});
