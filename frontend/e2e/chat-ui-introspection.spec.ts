import { type Page, type WebSocketRoute } from '@playwright/test';
import { expect, test } from './fixtures';

// JAN-285: Janus reads the page the user has open. The e2e account has no LLM,
// so the chat socket is replayed (`page.routeWebSocket`): it sends what /ws/chat
// sends for the `ui_introspection` tools (a tool `start` event, then the
// `__UI_INTROSPECT__` request) and finishes each call only once the mounted app
// answered with `ui_introspection_result`. First a `snapshot`, then
// `element_styles` for the snapshot's "Update Password" button.

const PLACEHOLDER = 'Ask a question...';
const QUESTION = 'why does the update password button look odd';
const TYPED_PASSWORD = 'hunter2-e2e-typed';

interface IntrospectionResult {
  type: string;
  request_id: string;
  ok: boolean;
  result: Record<string, unknown> | null;
  error: string | null;
}

interface OutlineNode {
  ref: string;
  role: string;
  name?: string;
  text?: string;
  input_type?: string;
}

const toolEvent = (tool: string, index: number, phase: 'start' | 'end') =>
  '__TOOL_EVENT__:' +
  JSON.stringify({
    call_id: `introspect-${index}`,
    tool_id: 'ui_introspection',
    tool_name: tool,
    args: {},
    args_truncated: false,
    result_truncated: false,
    phase,
    result_preview: phase === 'end' ? 'Done.' : null,
    duration_ms: phase === 'end' ? 12 : null,
  });

/** Replays one reply that takes a snapshot, then reads the styles of the node `styleTarget` names. */
const replayIntrospection = async (page: Page, styleTarget: string) => {
  const results: IntrospectionResult[] = [];
  await page.routeWebSocket(/\/ws\/chat/, (ws: WebSocketRoute) => {
    let started = false;
    ws.onMessage((raw) => {
      const frame = JSON.parse(typeof raw === 'string' ? raw : raw.toString()) as Record<string, unknown>;
      if (frame.type === 'ui_introspection_result') {
        const answer = frame as unknown as IntrospectionResult;
        results.push(answer);
        if (answer.request_id === 'snapshot-1') {
          ws.send(toolEvent('get_ui_snapshot', 0, 'end'));
          const nodes = (answer.result?.nodes as OutlineNode[] | undefined) ?? [];
          const target = nodes.find((node) => node.name === styleTarget);
          ws.send(toolEvent('get_element_styles', 1, 'start'));
          ws.send(
            '__UI_INTROSPECT__:' +
              JSON.stringify({ request_id: 'styles-1', kind: 'element_styles', params: { ref: target?.ref ?? 'missing' } })
          );
          return;
        }
        ws.send(toolEvent('get_element_styles', 1, 'end'));
        ws.send('I looked at your page.');
        ws.send('__END__');
        return;
      }
      if (started) return;
      started = true;
      ws.send('__CONVERSATION_ID__:282828');
      ws.send('__START__');
      ws.send(toolEvent('get_ui_snapshot', 0, 'start'));
      ws.send('__UI_INTROSPECT__:' + JSON.stringify({ request_id: 'snapshot-1', kind: 'snapshot', params: {} }));
    });
  });
  return results;
};

test('Janus reads the open page without its chat panel, passwords, private parts, or hidden text', async ({ page }) => {
  // Never submitted: a real password change would revoke the shared e2e session.
  await page.route('**/api/auth/change-password', (route) => route.abort());
  const results = await replayIntrospection(page, 'Update Password');

  await page.goto('/settings');
  await page.getByRole('tab', { name: 'Security' }).click();
  await expect(page.getByRole('heading', { name: 'Password & Session Security' })).toBeVisible();
  await page.getByLabel('Current Password').fill(TYPED_PASSWORD);
  // Content outside the app: a private subtree and a button with a hidden caption part.
  await page.evaluate(() => {
    const extra = document.createElement('div');
    extra.innerHTML =
      '<section data-janus-private><h2>Private ledger</h2><p>private balance 4711</p></section>' +
      '<button type="button"><span hidden>hidden caption part</span>Visible extra button</button>';
    document.body.append(extra);
  });

  const sidebar = page.getByRole('complementary', { name: 'Chat with Janus' });
  const input = sidebar.getByPlaceholder(PLACEHOLDER);
  await input.fill(QUESTION);
  await input.press('Enter');
  await expect(sidebar.getByText('I looked at your page.')).toBeVisible();

  expect(results.map((result) => [result.request_id, result.ok, result.error])).toEqual([
    ['snapshot-1', true, null],
    ['styles-1', true, null],
  ]);

  const snapshot = results[0].result as {
    route: string;
    viewport: Record<string, unknown>;
    nodes: OutlineNode[];
    skipped_private?: number;
  };
  expect(snapshot.route).toMatch(/^\/settings/);
  expect(snapshot.viewport).toMatchObject({ ui_shell: 'v1' });
  expect(snapshot.nodes.find((node) => node.name === 'Password & Session Security')).toMatchObject({ role: 'heading' });
  expect(snapshot.nodes.find((node) => node.name === 'Update Password')).toMatchObject({ role: 'button' });
  expect(snapshot.nodes.find((node) => node.name === 'Visible extra button')).toMatchObject({ role: 'button' });
  expect(snapshot.nodes.some((node) => node.input_type === 'password')).toBe(false);
  expect(snapshot.skipped_private).toBeGreaterThanOrEqual(1);

  const json = JSON.stringify(snapshot);
  for (const leaked of [
    TYPED_PASSWORD,
    'Current Password',
    QUESTION,
    'Chat with Janus',
    'Private ledger',
    'private balance 4711',
    'hidden caption part',
  ]) {
    expect(json).not.toContain(leaked);
  }

  const update = snapshot.nodes.find((node) => node.name === 'Update Password')!;
  const styles = results[1].result as { ref: string; tag: string; box: { width: number; height: number }; styles: Record<string, string> };
  expect(styles).toMatchObject({ ref: update.ref, tag: 'button' });
  expect(styles.box.width).toBeGreaterThan(0);
  expect(styles.box.height).toBeGreaterThan(0);
  expect(styles.styles.display).toBeTruthy();
  expect(JSON.stringify(styles)).not.toContain('url(');
});
