import { expect, test } from './fixtures';

const RELEASE_NOTES = {
  version: '1.13.0',
  changelog_url: '/docs/changelog',
  release_url: 'https://github.com/thomasniebler/janus/releases/tag/v1.13.0',
  releases: [
    {
      version: '1.13.0',
      date: '2026-11-11',
      groups: [
        { title: 'Features', items: ['**chat:** narrate multi-step turns', '**stats:** approval stats'] },
        { title: 'Bug Fixes', items: ['**db:** WAL mode for SQLite'] },
      ],
    },
  ],
};

test.describe('Version and what is new', () => {
  test('Settings shows the running version as a link to the in-app changelog', async ({ page }) => {
    await page.goto('/settings');

    const link = page.getByRole('link', { name: /^Janus v\d+\.\d+\.\d+ · Changelog$/ });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute('href', '/docs/changelog');

    await link.click();

    await expect(page).toHaveURL(/\/docs\/changelog$/);
    await expect(page.getByRole('heading', { name: 'Changelog', level: 1 })).toBeVisible();
  });

  test('flashes what changed after an update, links to the changelog and remembers the dismissal', async ({
    page,
  }) => {
    await page.route('**/api/status/version*', (route) => route.fulfill({ json: RELEASE_NOTES }));

    await page.goto('/');

    const flash = page.getByRole('status', { name: "What's new" });
    await expect(flash).toBeVisible();
    await expect(flash.getByText('Janus updated to v1.13.0')).toBeVisible();
    await expect(flash.getByText('chat: narrate multi-step turns')).toBeVisible();
    await expect(flash.getByText('db: WAL mode for SQLite')).toBeVisible();

    // Nothing is remembered while the notes are on screen: a reload would show them again.
    const storedKeys = () =>
      page.evaluate(() => Object.keys(window.localStorage).filter((key) => key.startsWith('janus.lastSeenVersion.')));
    expect(await storedKeys()).toHaveLength(0);

    await flash.getByRole('button', { name: 'Dismiss' }).click();

    await expect(flash).toBeHidden();
    await expect.poll(storedKeys).toHaveLength(1);
  });

  test('the flash links to the changelog page', async ({ page }) => {
    await page.route('**/api/status/version*', (route) => route.fulfill({ json: RELEASE_NOTES }));

    await page.goto('/');
    await page.getByRole('link', { name: 'Read the full changelog' }).click();

    await expect(page).toHaveURL(/\/docs\/changelog$/);
    await expect(page.getByRole('heading', { name: 'Changelog', level: 1 })).toBeVisible();
  });
});
