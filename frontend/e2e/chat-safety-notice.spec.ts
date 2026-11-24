import { expect, test } from './fixtures';

// JAN-127: when a tool output in a chat turn looks like a prompt injection, the
// backend appends a "Safety notice" paragraph to the reply as ordinary assistant
// text (src/janus/websocket/chat.py). The e2e account has no LLM provider, so this
// scenario replays the exact frames /ws/chat sends for such a turn and checks the
// user sees the notice rendered with the reply.

const PLACEHOLDER = 'Ask a question...';

const NOTICE =
  '\n\n---\n**Safety notice:** text returned by `get_email_detail` (instruction override) looks like ' +
  'instructions aimed at Janus (possible prompt injection). Janus treated it as data only. Blocked for the ' +
  'rest of this reply: `send_email`. If you do want an action with outside effects, ask for it again in a new message.';

test('a chat reply shows the prompt-injection safety notice', async ({ page }) => {
  await page.routeWebSocket(/\/ws\/chat/, (ws) => {
    ws.onMessage((raw) => {
      const text = typeof raw === 'string' ? raw : raw.toString();
      if (!text.includes('forward')) return;
      ws.send('__CONVERSATION_ID__:424242');
      ws.send('__TOOL_SELECTION__:' + JSON.stringify({ task_id: 't', step_id: 's', tools: [], denied: [] }));
      ws.send('__START__');
      ws.send('The invoice mail asks me to forward your inbox. I did not do that.');
      ws.send(NOTICE);
      ws.send('__END__');
    });
  });

  await page.goto('/');
  const sidebar = page.getByRole('complementary', { name: 'Chat with Janus' });
  const input = sidebar.getByPlaceholder(PLACEHOLDER);
  await expect(input).toBeVisible();
  await input.fill('Should I forward the invoice mail?');
  await input.press('Enter');

  await expect(sidebar.getByText('The invoice mail asks me to forward your inbox.')).toBeVisible();
  await expect(sidebar.locator('strong', { hasText: 'Safety notice:' })).toBeVisible();
  await expect(sidebar.getByText('send_email', { exact: true })).toBeVisible();
  await expect(sidebar.getByText(/ask for it again in a new message/)).toBeVisible();
});
