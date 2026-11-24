import type { Page } from '@playwright/test';
import { runBackendPython } from './backendSeed';
import { expect, test } from './fixtures';

/**
 * Adaptive character state (JAN-301): Janus proposes, the owner approves, rejects, rolls
 * back and resets on Settings -> Character. Each test runs as its own throwaway user (the
 * `asThrowawayUser` fixture), so the shared e2e admin's character is never touched. Proposing needs a live chat model, so
 * proposals are filed through the real proposal service, exactly as the chat tool does.
 */
const proposeAs = async (username: string, changes: Record<string, string | null>, rationale: string): Promise<number> =>
  Number(
    (
      await runBackendPython(
        `
import json
from janus.services import character_state_service
proposal = character_state_service.create_proposal(
    db, user.id, json.loads(args[0]), args[1], chat_turn_id="chat-conversation-1::message-1", conversation_id=None
)
print(proposal.id)
`,
        [JSON.stringify(changes), rationale],
        { as: username }
      )
    ).trim()
  );

/** The adaptive-state section of the Character tab. */
const panel = (page: Page) => page.getByRole('region', { name: 'Adaptive state' });

const openCharacterSettings = async (page: Page) => {
  await page.goto('/settings?tab=character');
  await expect(panel(page).getByRole('heading', { name: 'Adaptive state', exact: true })).toBeVisible();
};

test.describe('Adaptive character state (JAN-301)', () => {
  test.use({ asThrowawayUser: true });

  test('approves and rejects Janus proposals, showing proposed text as plain text', async ({ page, account }) => {
    const warmer = await proposeAs(account.username, { tone: 'warm' }, 'You asked for a friendlier tone.');
    const focus = await proposeAs(account.username, { current_focus: '<img src=x onerror=alert(1)> Tax return' }, 'Taxes came up twice.');
    await openCharacterSettings(page);

    const warmerCard = panel(page).getByRole('article', { name: `Proposal ${warmer}` });
    await expect(warmerCard.getByTestId('character-proposal-changes')).toHaveText('tone warm');
    await expect(warmerCard.getByTestId('character-proposal-rationale')).toHaveText('You asked for a friendlier tone.');
    const focusCard = panel(page).getByRole('article', { name: `Proposal ${focus}` });
    await expect(focusCard.getByTestId('character-proposal-changes')).toHaveText(
      'focus "<img src=x onerror=alert(1)> Tax return"'
    );
    await expect(focusCard.locator('img')).toHaveCount(0);

    await warmerCard.getByRole('button', { name: 'Approve' }).click();
    await expect(panel(page).getByRole('status')).toHaveText('Proposal approved.');
    await expect(warmerCard).toHaveCount(0);
    await expect(panel(page).getByRole('combobox', { name: 'Tone', exact: true })).toHaveValue('warm');
    await expect(panel(page).getByRole('listitem', { name: 'Revision 1' })).toContainText('Janus proposal, approved by you');

    await focusCard.getByRole('button', { name: 'Reject' }).click();
    await expect(panel(page).getByRole('status')).toHaveText('Proposal rejected.');
    await expect(focusCard).toHaveCount(0);
    await expect(panel(page).getByRole('textbox', { name: 'Current focus' })).toHaveValue('');
    await expect(panel(page).getByText('No open proposals.')).toBeVisible();
  });

  test('edits directly, restores an earlier state, resets, and refuses a proposal outside the bounds', async ({ page, account }) => {
    await openCharacterSettings(page);

    await panel(page).getByRole('combobox', { name: 'Tone', exact: true }).selectOption('formal');
    await panel(page).getByRole('button', { name: 'Save state' }).click();
    await expect(panel(page).getByRole('listitem', { name: 'Revision 1' })).toContainText('You edited the state');
    await panel(page).getByRole('combobox', { name: 'Verbosity', exact: true }).selectOption('detailed');
    await panel(page).getByRole('button', { name: 'Save state' }).click();
    await expect(panel(page).getByRole('listitem', { name: 'Revision 2' })).toContainText('verbosity detailed');

    await panel(page).getByRole('listitem', { name: 'Revision 1' }).getByRole('button', { name: 'Restore' }).click();
    await expect(panel(page).getByRole('listitem', { name: 'Revision 3' })).toContainText('You restored an earlier state');
    await expect(panel(page).getByRole('combobox', { name: 'Tone', exact: true })).toHaveValue('formal');
    await expect(panel(page).getByRole('combobox', { name: 'Verbosity', exact: true })).toHaveValue('balanced');

    await panel(page).getByRole('button', { name: 'Reset to defaults' }).click();
    await expect(panel(page).getByRole('listitem', { name: 'Revision 4' })).toContainText('You reset to the defaults');
    await expect(panel(page).getByRole('combobox', { name: 'Tone', exact: true })).toHaveValue('neutral');

    const playful = await proposeAs(account.username, { tone: 'playful' }, 'Lighten things up.');
    await panel(page).getByLabel('Allow tone playful').uncheck();
    await panel(page).getByRole('button', { name: 'Save bounds' }).click();
    await expect(panel(page).getByRole('status')).toHaveText('Bounds saved.');
    await expect(panel(page).getByRole('combobox', { name: 'Tone', exact: true }).locator('option[value="playful"]')).toHaveCount(0);

    const card = panel(page).getByRole('article', { name: `Proposal ${playful}` });
    await card.getByRole('button', { name: 'Approve' }).click();
    await expect(panel(page).getByRole('alert')).toContainText("tone 'playful' is outside the allowed values");
    await expect(panel(page).getByRole('combobox', { name: 'Tone', exact: true })).toHaveValue('neutral');
    await expect(card).toBeVisible();
  });

  test('loads older history on demand and restores the very first state', async ({ page, account }) => {
    // 55 owner edits: more than the first history page (50) holds.
    await runBackendPython(
      `
from janus.services import character_state_service
for index in range(55):
    character_state_service.set_state(
        db, user.id,
        {"tone": "warm" if index % 2 else "formal", "verbosity": "balanced", "proactivity": "balanced",
         "current_focus": f"Step {index}"},
    )
`,
      [],
      { as: account.username }
    );
    await openCharacterSettings(page);

    await expect(panel(page).getByRole('listitem', { name: 'Revision 55', exact: true })).toBeVisible();
    await expect(panel(page).getByRole('listitem', { name: 'Revision 6', exact: true })).toBeVisible();
    await expect(panel(page).getByRole('listitem', { name: 'Revision 5', exact: true })).toHaveCount(0);

    await panel(page).getByRole('button', { name: 'Load older changes' }).click();
    const first = panel(page).getByRole('listitem', { name: 'Revision 1', exact: true });
    await expect(first).toContainText('focus "Step 0"');
    await expect(panel(page).getByRole('button', { name: 'Load older changes' })).toHaveCount(0);

    await first.getByRole('button', { name: 'Restore' }).click();
    await expect(panel(page).getByRole('listitem', { name: 'Revision 56', exact: true })).toContainText('You restored an earlier state');
    await expect(panel(page).getByRole('textbox', { name: 'Current focus' })).toHaveValue('Step 0');
  });
});

