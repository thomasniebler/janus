import { type Page, type WebSocket } from '@playwright/test';
import { expect, test } from './fixtures';
import { deferred } from './helpers';
import { isBriefingRequest, LLMStub } from './llm-stub';

const waitForBriefingSocket = (page: Page) =>
  page.waitForEvent('websocket', { predicate: (socket) => socket.url().includes('/ws/briefing') });

/** The backend closes `/ws/briefing` once it sent the briefing or its error. */
const waitForClose = async (socket: WebSocket) => {
  if (!socket.isClosed()) await socket.waitForEvent('close', { timeout: 30_000 });
};

test.describe('Morning Briefing page', () => {
  // Briefing runs are the user's own: as a throwaway user each test starts with no run today,
  // whatever other specs ran on the shared e2e admin (JAN-336).
  test.use({ asThrowawayUser: true });

  // The e2e backend now allow-lists its own origin (see env.ts's ALLOWED_ORIGINS), so the
  // `/ws/briefing` socket handshake succeeds for real here rather than being rejected at the
  // Origin check. This account has no LLM provider configured, so the Morning briefing trigger
  // run the socket starts fails (Ollama unreachable) -- the app's real "LLM not reachable" error
  // path (the run's error mapped by `user_facing_llm_error` in src/janus/llm_service.py), not a
  // harness artifact.
  test('renders the briefing page and surfaces the LLM provider error', async ({ page }) => {
    const pageLoadSocket = waitForBriefingSocket(page);
    await page.goto('/briefing');

    await expect(page.getByRole('heading', { name: 'Morning Briefing' })).toBeVisible();
    await expect(page.getByText('Janus Analysis')).toBeVisible();

    // Opening the page shows today's briefing when one already ran (another spec may have run
    // it with a scripted model), so refresh once the page's own load is done: that always runs
    // the trigger again. The real (unreachable) LLM provider connection attempt can take a few
    // seconds to time out, so this needs more than Playwright's default 5s assertion timeout.
    await waitForClose(await pageLoadSocket);
    const refreshButton = page.getByRole('button', { name: 'Refresh briefing' });
    await refreshButton.click();

    const errorMessage = page.getByText(/janus cannot reach the configured llm provider/i);
    await expect(errorMessage).toBeVisible({ timeout: 15_000 });
    const retryButton = page.getByRole('button', { name: 'Retry' });
    await expect(retryButton).toBeVisible();

    // Retrying re-opens the socket, which runs the briefing again and fails the same way.
    await retryButton.click();
    await expect(errorMessage).toBeVisible({ timeout: 15_000 });
  });

  // JAN-220: with the backend's model pointed at a scripted OpenAI-compatible stub, the real
  // Morning briefing trigger run completes. `/ws/briefing` sends the finished run's text as
  // one frame between `__START__` and `__END__` (the briefing is a trigger run, JAN-231; the
  // model's tokens are not relayed one by one -- token streaming is covered in chat.spec.ts).
  test.describe('with a scripted model', () => {
    const stub = new LLMStub();
    test.beforeAll(() => stub.start());
    test.afterAll(() => stub.stop());
    // The backend model is instance-wide, so the admin sets and resets it.
    test.afterEach(async ({ adminRequest }) => {
      await LLMStub.resetBackendModel(adminRequest);
    });

    const BRIEFING_CHUNKS = [
      '## Focus today\n\n',
      '- Finish the **quarterly report** draft\n',
      '- Call the dentist\n\n',
      'Nothing else needs preparation in the next weeks.',
    ];

    test('runs the briefing trigger and renders the complete briefing, then reuses it', async ({ page, adminRequest }) => {
      stub.reply(() => ({ chunks: ['An earlier briefing.'] }));
      await stub.useAsBackendModel(adminRequest);
      const frames: string[] = [];
      page.on('websocket', (socket) => {
        if (socket.url().includes('/ws/briefing')) {
          socket.on('framereceived', (frame) => frames.push(String(frame.payload)));
        }
      });

      const pageLoadSocket = waitForBriefingSocket(page);
      await page.goto('/briefing');
      // The page's own load settles first (it shows today's briefing or runs one).
      await waitForClose(await pageLoadSocket);
      const refreshButton = page.getByRole('button', { name: 'Refresh briefing' });
      await expect(refreshButton).toBeEnabled();

      const gate = deferred();
      stub.reply((request) =>
        isBriefingRequest(request) ? { chunks: BRIEFING_CHUNKS, gate: gate.promise } : { chunks: ['(unexpected request)'] },
      );
      const requestsBefore = stub.requests.length;
      frames.length = 0;
      await refreshButton.click();

      // While the trigger run waits for the model, the page shows it is working.
      await expect.poll(() => stub.requests.slice(requestsBefore).some(isBriefingRequest)).toBe(true);
      await expect(refreshButton).toBeDisabled();
      await expect(page.getByText('An earlier briefing.')).toHaveCount(0);
      expect(frames).toEqual(['__START__']);

      gate.release();
      await expect(page.getByRole('heading', { name: 'Focus today' })).toBeVisible({ timeout: 15_000 });
      await expect(page.getByRole('listitem').filter({ hasText: 'Finish the quarterly report draft' })).toBeVisible();
      await expect(page.locator('strong', { hasText: 'quarterly report' })).toBeVisible();
      await expect(page.getByRole('listitem').filter({ hasText: 'Call the dentist' })).toBeVisible();
      await expect(page.getByText('Nothing else needs preparation in the next weeks.')).toBeVisible();
      await expect(refreshButton).toBeEnabled();
      expect(frames).toEqual(['__START__', BRIEFING_CHUNKS.join(''), '__END__']);
      await expect(page.getByText(/janus cannot reach/i)).toHaveCount(0);

      // Opening the page again shows today's run without asking the model again.
      const requestsAfterRun = stub.requests.length;
      await page.reload();
      await expect(page.getByRole('heading', { name: 'Focus today' })).toBeVisible();
      await expect(refreshButton).toBeEnabled();
      expect(stub.requests.length).toBe(requestsAfterRun);
    });
  });
});
