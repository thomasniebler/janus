import { type Page, type WebSocketRoute } from '@playwright/test';
import { expect, test } from './fixtures';

// Progress narration: on a multi-step turn Janus writes a short plan before its first
// tool call, one line after each round, then the answer. The backend
// (plan_step_events.RoundTextJoiner) streams a blank line between rounds, so each part
// renders as its own paragraph. The e2e account has no LLM provider, so the turn
// replays the frames /ws/chat sends (page.routeWebSocket).

const PLACEHOLDER = 'Ask a question...';

const PLAN = 'Plan: I will check your notes first, then your calendar.';
const PROGRESS = 'Found 3 notes, as expected; the newest mentions Friday. Next: the calendar.';
const ANSWER = 'You are free on Friday afternoon.';

const planEvent = (stepId: string, index: number, status: string, toolNames: string[]) =>
  '__PLAN_EVENT__:' + JSON.stringify({ step_id: stepId, index, status, tool_names: toolNames });

const toolEvent = (callId: string, toolName: string, stepId: string, phase: string) =>
  '__TOOL_EVENT__:' +
  JSON.stringify({
    call_id: callId,
    tool_id: null,
    tool_name: toolName,
    phase,
    args: {},
    args_truncated: false,
    result_preview: phase === 'start' ? null : 'ok',
    result_truncated: false,
    duration_ms: phase === 'start' ? null : 7,
    approval: null,
    step_id: stepId,
  });

const runStep = (ws: WebSocketRoute, stepId: string, index: number, callId: string, toolName: string) => {
  ws.send(planEvent(stepId, index, 'pending', [toolName]));
  ws.send(planEvent(stepId, index, 'running', [toolName]));
  ws.send(toolEvent(callId, toolName, stepId, 'start'));
  ws.send(toolEvent(callId, toolName, stepId, 'end'));
  ws.send(planEvent(stepId, index, 'completed', [toolName]));
};

const routeChat = async (page: Page) => {
  await page.routeWebSocket(/\/ws\/chat/, (ws: WebSocketRoute) => {
    ws.onMessage(() => {
      ws.send('__CONVERSATION_ID__:424317');
      ws.send('__START__');
      // Round 1: the plan, streamed in chunks, then its tool call.
      ws.send('Plan: I will check your notes first, ');
      ws.send('then your calendar.');
      runStep(ws, 'step-1', 1, 'run-1', 'list_memory_facts');
      // Round 2: the paragraph break the backend puts in front of a new round's text.
      ws.send('\n\n');
      ws.send(PROGRESS);
      runStep(ws, 'step-2', 2, 'run-2', 'list_calendar_events');
      // Final round: the answer.
      ws.send('\n\n');
      ws.send(ANSWER);
      ws.send('__END__');
    });
  });
};

test('a multi-step turn shows its plan, progress line, and answer as separate paragraphs', async ({ page }) => {
  await routeChat(page);

  await page.goto('/chat?ui=v2');
  const chatPage = page.getByTestId('chat-page');
  const input = chatPage.getByPlaceholder(PLACEHOLDER);
  await input.fill('When am I free this week?');
  await input.press('Enter');

  for (const text of [PLAN, PROGRESS, ANSWER]) {
    await expect(chatPage.locator('p', { hasText: text })).toHaveCount(1);
  }
  // No paragraph runs two parts together.
  await expect(chatPage.locator('p', { hasText: 'calendar.Found' })).toHaveCount(0);
  await expect(chatPage.locator('p', { hasText: PLAN }).filter({ hasText: ANSWER })).toHaveCount(0);
  // The turn's steps still show in the timeline, beside the narration.
  await expect(chatPage.getByTestId('turn-timeline-step')).toHaveCount(2);
});
