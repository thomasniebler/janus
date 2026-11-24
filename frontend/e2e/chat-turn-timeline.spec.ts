import { type Page, type WebSocketRoute } from '@playwright/test';
import { expect, test } from './fixtures';

// JAN-316: the current turn's plan steps. Each model round that asks for tools is a
// step (`__PLAN_EVENT__`), its calls carry its `step_id`, and a step whose call waits
// for Approve / Deny (`__TOOL_APPROVAL__`, JAN-254) shows as waiting. The e2e account
// has no LLM provider, so the turn replays the frames /ws/chat sends
// (page.routeWebSocket) and finishes like the backend does once the card is answered.

const PLACEHOLDER = 'Ask a question...';

const planEvent = (stepId: string, index: number, status: string, toolNames: string[]) =>
  '__PLAN_EVENT__:' + JSON.stringify({ step_id: stepId, index, status, tool_names: toolNames });

const toolEvent = (callId: string, toolId: string, toolName: string, stepId: string, phase: string, extra = {}) =>
  '__TOOL_EVENT__:' +
  JSON.stringify({
    call_id: callId,
    tool_id: toolId,
    tool_name: toolName,
    phase,
    args: {},
    args_truncated: false,
    result_preview: phase === 'start' ? null : 'ok',
    result_truncated: false,
    duration_ms: phase === 'start' ? null : 7,
    approval: null,
    step_id: stepId,
    ...extra,
  });

const routeChat = async (page: Page) => {
  await page.routeWebSocket(/\/ws\/chat/, (ws: WebSocketRoute) => {
    ws.onMessage((raw) => {
      const frame = JSON.parse(typeof raw === 'string' ? raw : raw.toString()) as Record<string, unknown>;
      if (frame.type === 'tool_approval') {
        ws.send(toolEvent('run-2', 'mail.send', 'send_email', 'step-2', 'end', { approval: 'approved' }));
        ws.send(planEvent('step-2', 2, 'completed', ['send_email']));
        ws.send('I checked your notes and sent the mail.');
        ws.send('__END__');
        return;
      }
      ws.send('__CONVERSATION_ID__:424316');
      ws.send('__START__');
      // Step 1: one call, finished.
      ws.send(planEvent('step-1', 1, 'pending', ['list_memory_facts']));
      ws.send(planEvent('step-1', 1, 'running', ['list_memory_facts']));
      ws.send(toolEvent('run-1', 'memory', 'list_memory_facts', 'step-1', 'start'));
      ws.send(toolEvent('run-1', 'memory', 'list_memory_facts', 'step-1', 'end'));
      ws.send(planEvent('step-1', 1, 'completed', ['list_memory_facts']));
      // Step 2: a confirmation-required call paused for approval.
      ws.send(planEvent('step-2', 2, 'pending', ['send_email']));
      ws.send(planEvent('step-2', 2, 'running', ['send_email']));
      ws.send(toolEvent('run-2', 'mail.send', 'send_email', 'step-2', 'start'));
      ws.send(
        '__TOOL_APPROVAL__:' +
          JSON.stringify({
            call_id: 'run-2',
            tool_id: 'mail.send',
            tool_name: 'send_email',
            display_name: 'Send mail',
            risk_level: 'high',
            reason: 'Send email from your connected accounts.',
            args: {},
            args_truncated: false,
            timeout_seconds: 300,
          })
      );
    });
  });
};

test('the /chat page docks both steps live, with the waiting approval as waiting', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await routeChat(page);

  await page.goto('/chat?ui=v2');
  const chatPage = page.getByTestId('chat-page');
  const input = chatPage.getByPlaceholder(PLACEHOLDER);
  await input.fill('Check my notes and mail my boss');
  await input.press('Enter');

  // Docked at xl: one expanded timeline, beside the conversation.
  const timeline = chatPage.getByRole('complementary', { name: 'Plan steps' }).getByTestId('turn-timeline');
  await expect(chatPage.getByTestId('turn-timeline')).toHaveCount(1);
  const steps = timeline.getByTestId('turn-timeline-step');
  await expect(steps).toHaveCount(2);
  await expect(steps.nth(0)).toContainText('Step 1· completed');
  await expect(steps.nth(0).getByLabel('list_memory_facts, done')).toBeVisible();
  await expect(steps.nth(1)).toContainText('Step 2· waiting for approval');
  await expect(steps.nth(1).getByLabel('send_email, waiting for approval')).toBeVisible();
  // Step frames are never chat text.
  await expect(chatPage.getByText(/__PLAN_EVENT__/)).toHaveCount(0);

  await chatPage.getByRole('group', { name: 'Janus wants to use Send mail' }).getByRole('button', { name: 'Approve' }).click();
  await expect(chatPage.getByText('I checked your notes and sent the mail.')).toBeVisible();
  await expect(steps.nth(1)).toContainText('Step 2· completed');
  await expect(steps.nth(1).getByLabel('send_email, done')).toBeVisible();
});

test('the sidebar chat shows the timeline collapsed until opened', async ({ page }) => {
  await routeChat(page);

  await page.goto('/');
  const sidebar = page.getByRole('complementary', { name: 'Chat with Janus' });
  const input = sidebar.getByPlaceholder(PLACEHOLDER);
  await input.fill('Check my notes and mail my boss');
  await input.press('Enter');

  const toggle = sidebar.getByRole('button', { name: /This turn/ });
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(toggle).toContainText('2 steps · step 2 waiting for approval');
  await expect(sidebar.getByTestId('turn-timeline-step')).toHaveCount(0);

  await toggle.click();
  const steps = sidebar.getByTestId('turn-timeline-step');
  await expect(steps).toHaveCount(2);
  await expect(steps.nth(0)).toContainText('completed');
  await expect(steps.nth(1)).toContainText('waiting for approval');
});
