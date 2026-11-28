import { expect, test } from './fixtures';
import { api } from './helpers';

// JAN-253: Janus generates an image with a cloud model and shows it inline. The e2e
// account has no LLM or image provider, so the live turn replays the exact frames
// /ws/chat sends for a generate_image call (via page.routeWebSocket), and the stored
// image is served by a route for its /api/images/<id> URL. The reload is served the
// stored `ui_blocks` row the backend returns for that conversation. The real endpoint
// is checked too: an id the user does not own is a 404.

const PLACEHOLDER = 'Ask a question...';
const CONVERSATION_ID = 424253;
const IMAGE_ID = 'e2eImage_0123456789abcdefghijkl';
const IMAGE_URL = `/api/images/${IMAGE_ID}`;
// A 2x2 PNG.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAAAAABX3VL4AAAADklEQVR4nGNoaGBoaAAABgYCASzBUNcAAAAASUVORK5CYII=',
  'base64',
);

const block = {
  id: 'blk_image0000253',
  version: 1,
  type: 'image',
  alt: 'A lighthouse at dusk, watercolor',
  url: IMAGE_URL,
  caption: 'Generated with OpenAI gpt-image-1',
};
const imageCall = (phase: 'start' | 'end') => ({
  call_id: 'run-img-1',
  tool_id: 'images',
  tool_name: 'generate_image',
  phase,
  args: { prompt: 'A lighthouse at dusk', style: 'watercolor' },
  args_truncated: false,
  result_preview: phase === 'end' ? `Generated and shown to the user: image ${IMAGE_ID}` : null,
  result_truncated: false,
  duration_ms: phase === 'end' ? 4200 : null,
});
const REPLY = 'Here is your lighthouse.';

test('a generated image renders inline with a download link, live and after a reload', async ({ page }) => {
  await page.route(`**${IMAGE_URL}`, (route) =>
    route.fulfill({ body: PNG, contentType: 'image/png', headers: { 'Cache-Control': 'no-store' } }),
  );
  await page.routeWebSocket(/\/ws\/chat/, (ws) => {
    ws.onMessage((raw) => {
      const text = typeof raw === 'string' ? raw : raw.toString();
      if (!text.includes('lighthouse')) return;
      ws.send(`__CONVERSATION_ID__:${CONVERSATION_ID}`);
      ws.send('__TOOL_SELECTION__:' + JSON.stringify({ task_id: 't', step_id: 's', tools: [], denied: [] }));
      ws.send('__START__');
      ws.send(REPLY);
      ws.send('__TOOL_EVENT__:' + JSON.stringify(imageCall('start')));
      ws.send('__TOOL_EVENT__:' + JSON.stringify(imageCall('end')));
      ws.send('__UI_BLOCK__:' + JSON.stringify(block));
      ws.send('__END__');
    });
  });

  await page.goto('/');
  const sidebar = page.getByRole('complementary', { name: 'Chat with Janus' });
  const input = sidebar.getByPlaceholder(PLACEHOLDER);
  await expect(input).toBeVisible();
  await input.fill('Draw a lighthouse at dusk');
  await input.press('Enter');

  const checkImage = async () => {
    await expect(sidebar.getByText(REPLY)).toBeVisible();
    const region = sidebar.getByRole('region', { name: 'Image' });
    const image = region.getByRole('img', { name: 'A lighthouse at dusk, watercolor' });
    await expect(image).toHaveAttribute('src', IMAGE_URL);
    // The bytes actually loaded (a broken image would fall back to its alt text).
    await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBe(2);
    await expect(region.getByText('Generated with OpenAI gpt-image-1')).toBeVisible();
    const download = region.getByRole('link', { name: 'Download' });
    await expect(download).toHaveAttribute('href', IMAGE_URL);
    await expect(download).toHaveAttribute('download', '');
    await expect(sidebar.getByText('Image unavailable')).toHaveCount(0);
    await expect(sidebar.getByText(/__UI_BLOCK__/)).toHaveCount(0);
  };
  await checkImage();

  await page.route(`**/api/conversations/${CONVERSATION_ID}`, (route) =>
    route.fulfill({
      json: {
        id: CONVERSATION_ID,
        title: 'Lighthouse',
        created_at: '2026-10-05T08:00:00Z',
        updated_at: '2026-10-05T08:01:00Z',
        messages: [
          {
            id: 1,
            role: 'user',
            content: 'Draw a lighthouse at dusk',
            created_at: '2026-10-05T08:00:00Z',
            tool_calls: [],
            ui_blocks: [],
          },
          {
            id: 2,
            role: 'assistant',
            content: REPLY,
            created_at: '2026-10-05T08:00:05Z',
            tool_calls: [imageCall('end')],
            ui_blocks: [{ block_id: block.id, block_type: 'image', schema_version: 1, payload: block }],
          },
        ],
      },
    }),
  );
  await page.reload();
  await checkImage();
});

test('the real image endpoint never serves an image the user does not own', async ({ page }) => {
  await page.goto('/');
  const response = await page.request.get(`/api/images/${IMAGE_ID}`);
  expect(response.status()).toBe(404);
  expect(response.headers()['content-type']).toContain('application/json');
});

test.describe('Image generation settings', () => {
  // The image provider is instance-wide: put it back to off even when the test stops half-way,
  // so no later spec sees an image provider (JAN-336).
  test.afterEach(async ({ adminRequest }) => {
    await api(adminRequest, 'patch', '/api/planner/settings', { image_provider: null, image_model: null });
  });

  test('the Image generation card lists every AI provider, lets only cloud ones be chosen, and persists the choice', async ({ page }) => {
    await page.goto('/settings?tab=system&sub=capabilities');
    await expect(page.getByRole('heading', { name: 'Image generation' })).toBeVisible();
    const provider = page.getByLabel('Image provider');
    const save = page.getByRole('button', { name: 'Save image generation' });

    await expect(provider).toHaveValue('');
    await expect(provider.locator('option:not([disabled])')).toHaveText([
      'Off (no image generation)',
      'OpenAI',
      'Google Gemini',
      'OpenRouter',
    ]);
    await expect(provider.locator('option[disabled]')).toHaveText([
      'Ollama / Llama (cannot generate images)',
      'Anthropic (cannot generate images)',
      'GitHub Models (cannot generate images)',
      'Azure OpenAI (cannot generate images)',
    ]);
    await provider.selectOption('openai');
    await expect(page.getByLabel('Image model')).toHaveAttribute('placeholder', 'gpt-image-1');
    await expect(page.getByTestId('image-cloud-hint')).toContainText('sent to OpenAI');
    // The e2e instance has no OpenAI key, so the card says image generation cannot run yet.
    await expect(page.getByTestId('image-key-missing')).toBeVisible();
    await save.click();
    await expect(page.getByText('Image generation settings saved.')).toBeVisible();

    await page.reload();
    await expect(provider).toHaveValue('openai');
    await expect(page.getByLabel('Image model')).toHaveValue('gpt-image-1');

    // Back to off through the API: the card follows it after a reload.
    await api(page, 'patch', '/api/planner/settings', { image_provider: null });
    await page.reload();
    await expect(provider).toHaveValue('');
  });
});
