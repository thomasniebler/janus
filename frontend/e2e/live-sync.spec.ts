import { expect, test } from './fixtures';

// JAN-104: a write made outside the open view (a chat tool, a trigger run, another tab)
// reaches the page over /ws/changes and refreshes it without a reload.
test.describe('Live domain-change sync', () => {
  // The fact belongs to a throwaway user, so a failure never leaves it on the shared admin (JAN-336).
  test.use({ asThrowawayUser: true });

  test('a memory fact created elsewhere appears on the open Memory page', async ({ page }) => {
    const summary = `E2E live fact ${Date.now()}`;
    // Listen from the moment the socket opens: the ready frame follows the handshake at once.
    const subscribed = new Promise<void>((resolve) => {
      page.on('websocket', (ws) => {
        if (!ws.url().endsWith('/ws/changes')) return;
        ws.on('framereceived', (frame) => {
          if (String(frame.payload).includes('"ready"')) resolve();
        });
      });
    });

    await page.goto('/memory');
    await expect(page.getByRole('heading', { name: 'Agent Memory' })).toBeVisible();
    await subscribed;

    const { csrf_token: csrfToken } = await (await page.request.get('/api/auth/csrf')).json();
    const created = await page.request.post('/api/memory/', {
      data: { summary },
      headers: { 'X-CSRF-Token': csrfToken },
    });
    expect(created.ok()).toBeTruthy();

    await expect(page.getByText(summary, { exact: true })).toBeVisible();

    await page.getByRole('button', { name: `Delete memory fact "${summary}"` }).click();
    const confirm = page.getByRole('dialog', { name: 'Delete memory fact?' });
    await confirm.getByRole('button', { name: 'Delete', exact: true }).click();
    await expect(page.getByText(summary, { exact: true })).toHaveCount(0);
  });
});
