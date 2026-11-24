import { expect, test } from './fixtures';
import { confirmDialog } from './helpers';

// JAN-359: the blanket "Unattended: Allow everything" / "In chat: Allow all" modes and the per-tool
// "Always allow" In chat value. A throwaway user owns the settings, so nothing leaks into other specs.
test.use({ asThrowawayUser: true });

test('asks before Allow everything, saves it, and lets the trigger editor offer image generation', async ({ page }) => {
  await page.goto('/settings?tab=tools');
  const unattended = page.getByRole('combobox', { name: 'Unattended', exact: true });
  await expect(unattended).toHaveValue('per_tool');

  // Cancelling the warning saves nothing.
  await unattended.selectOption('allow_all');
  const dialog = confirmDialog(page, 'Allow everything unattended?');
  await expect(dialog).toContainText('with nobody watching');
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toHaveCount(0);
  await expect(unattended).toHaveValue('per_tool');

  await unattended.selectOption('allow_all');
  await dialog.getByRole('button', { name: 'Allow', exact: true }).click();
  await expect(page.getByText('every tool runs unattended')).toBeVisible();
  await expect(page.getByText('Allowed unattended by “Allow everything”.').first()).toBeVisible();
  await page.reload();
  await expect(page.getByRole('combobox', { name: 'Unattended', exact: true })).toHaveValue('allow_all');

  // Image generation is no longer hidden from a new trigger's tool list.
  await page.goto('/settings?tab=runtime');
  await page.getByRole('button', { name: 'New trigger' }).click();
  await expect(page.getByRole('checkbox', { name: /Image generation/ })).toBeVisible();

  // Back to per-tool switches without a warning.
  await page.goto('/settings?tab=tools');
  await page.getByRole('combobox', { name: 'Unattended', exact: true }).selectOption('per_tool');
  await expect(page.getByText('every tool runs unattended')).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('combobox', { name: 'Unattended', exact: true })).toHaveValue('per_tool');
});

test('switches chat to Allow all and a single tool to Always allow', async ({ page }) => {
  await page.goto('/settings?tab=tools');
  const chat = page.getByRole('combobox', { name: 'In chat', exact: true });
  await chat.selectOption('allow_all');
  const dialog = confirmDialog(page, 'Allow all tools in chat without asking?');
  await expect(dialog).toContainText('No tool asks for your OK in chat');
  await dialog.getByRole('button', { name: 'Allow', exact: true }).click();
  await expect(page.getByText('no approval cards')).toBeVisible();
  await page.reload();
  await expect(page.getByRole('combobox', { name: 'In chat', exact: true })).toHaveValue('allow_all');
  await page.getByRole('combobox', { name: 'In chat', exact: true }).selectOption('per_tool');
  await expect(page.getByText('no approval cards')).toHaveCount(0);

  const sendMail = page.getByRole('combobox', { name: 'In chat: Send mail' });
  await sendMail.selectOption('always');
  await expect(page.getByRole('button', { name: 'Reset Send mail' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('combobox', { name: 'In chat: Send mail' })).toHaveValue('always');
  await page.getByRole('button', { name: 'Reset Send mail' }).click();
  await expect(page.getByRole('combobox', { name: 'In chat: Send mail' })).toHaveValue('ask');
});
