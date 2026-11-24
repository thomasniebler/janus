import { expect, test } from './fixtures';

// JAN-252: Janus can answer with validated UI blocks. The e2e account has no LLM
// provider, so the live turn replays the exact frames /ws/chat sends for a reply with a
// card and a table (via page.routeWebSocket); the reload is served the stored
// `ui_blocks` rows the backend returns for that conversation.

const PLACEHOLDER = 'Ask a question...';
const CONVERSATION_ID = 424252;

const card = {
  id: 'blk_card00000001',
  version: 1,
  type: 'card',
  title: 'Invoice overdue',
  subtitle: 'billing@example.com',
  body: 'Payment of <b>120 EUR</b> was due on Friday.',
  fields: [{ label: 'Amount', value: '120 EUR' }],
  links: [{ label: 'Open in Emails', href: '/emails' }],
};
const table = {
  id: 'blk_table0000001',
  version: 1,
  type: 'table',
  title: 'Open tasks',
  columns: ['Task', 'Due'],
  rows: [
    ['Pay invoice', 'Fri'],
    ['Call Anna', null],
  ],
};
const renderCall = (callId: string, block: object, phase: 'start' | 'end') => ({
  call_id: callId,
  tool_id: 'ui_blocks',
  tool_name: 'render_ui',
  phase,
  args: { block },
  args_truncated: false,
  result_preview: phase === 'end' ? `Shown to the user: block` : null,
  result_truncated: false,
  duration_ms: phase === 'end' ? 3 : null,
});
const REPLY = 'Here is the overdue invoice and your open tasks.';

test('chat replies render UI blocks as text, live and after a reload', async ({ page }) => {
  await page.routeWebSocket(/\/ws\/chat/, (ws) => {
    ws.onMessage((raw) => {
      const text = typeof raw === 'string' ? raw : raw.toString();
      if (!text.includes('invoice')) return;
      ws.send(`__CONVERSATION_ID__:${CONVERSATION_ID}`);
      ws.send('__TOOL_SELECTION__:' + JSON.stringify({ task_id: 't', step_id: 's', tools: [], denied: [] }));
      ws.send('__START__');
      ws.send(REPLY);
      for (const [callId, block] of [
        ['run-1', card],
        ['run-2', table],
      ] as const) {
        ws.send('__TOOL_EVENT__:' + JSON.stringify(renderCall(callId, block, 'start')));
        ws.send('__TOOL_EVENT__:' + JSON.stringify(renderCall(callId, block, 'end')));
        ws.send('__UI_BLOCK__:' + JSON.stringify(block));
      }
      ws.send('__END__');
    });
  });

  await page.goto('/');
  const sidebar = page.getByRole('complementary', { name: 'Chat with Janus' });
  const input = sidebar.getByPlaceholder(PLACEHOLDER);
  await expect(input).toBeVisible();
  await input.fill('Show the overdue invoice and my tasks');
  await input.press('Enter');

  const checkBlocks = async () => {
    await expect(sidebar.getByText(REPLY)).toBeVisible();
    const cardRegion = sidebar.getByRole('region', { name: 'Card' });
    await expect(cardRegion.getByRole('heading', { name: 'Invoice overdue' })).toBeVisible();
    // Model text is plain text: the markup is shown, never rendered.
    await expect(cardRegion.getByText('Payment of <b>120 EUR</b> was due on Friday.')).toBeVisible();
    await expect(cardRegion.locator('b')).toHaveCount(0);
    await expect(cardRegion.getByRole('link', { name: 'Open in Emails' })).toHaveAttribute('href', '/emails');
    const tableRegion = sidebar.getByRole('region', { name: 'Table: Open tasks' });
    await expect(tableRegion.getByRole('columnheader', { name: 'Task' })).toBeVisible();
    await expect(tableRegion.getByRole('cell', { name: 'Call Anna' })).toBeVisible();
    await expect(sidebar.getByText(/__UI_BLOCK__/)).toHaveCount(0);
    await expect(sidebar.getByText('Could not render this block')).toHaveCount(0);
  };
  await checkBlocks();

  // After a reload the same blocks come from the stored ui_blocks rows.
  await page.route(`**/api/conversations/${CONVERSATION_ID}`, (route) =>
    route.fulfill({
      json: {
        id: CONVERSATION_ID,
        title: 'Invoice',
        created_at: '2026-10-05T08:00:00Z',
        updated_at: '2026-10-05T08:01:00Z',
        messages: [
          {
            id: 1,
            role: 'user',
            content: 'Show the overdue invoice and my tasks',
            created_at: '2026-10-05T08:00:00Z',
            tool_calls: [],
            ui_blocks: [],
          },
          {
            id: 2,
            role: 'assistant',
            content: REPLY,
            created_at: '2026-10-05T08:00:05Z',
            tool_calls: [renderCall('run-1', card, 'end'), renderCall('run-2', table, 'end')],
            ui_blocks: [card, table].map((block) => ({
              block_id: block.id,
              block_type: block.type,
              schema_version: 1,
              payload: block,
            })),
          },
        ],
      },
    })
  );
  await page.reload();
  await checkBlocks();
});
