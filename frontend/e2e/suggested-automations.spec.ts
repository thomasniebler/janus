import { runBackendPython } from './backendSeed';
import { expect, test } from './fixtures';

/**
 * Seeds two automations as Janus would draft them from chat (JAN-235), through the
 * real proposal service, and switches "Relabel mail" off for unattended runs so the
 * first one shows a blocked tool. Drafting needs a live chat model, so the service is
 * called directly; the review flow below then runs against the real backend.
 */
const SEED_SCRIPT = `
from janus import tool_policy
from janus import trigger_proposal_service as proposals

tool_policy.set_policy(db, user.id, "mail.label", False)
proposals.create_proposal(
    db, user.id, name="E2E newsletter labels",
    objective="Every morning, relabel newsletters as DIGEST.",
    tool_ids=["core", "mail", "mail.label"], kind="schedule",
    schedule={"type": "daily", "time": "07:00", "days": []}, timezone="Europe/Berlin",
    rationale="You said newsletters clutter your inbox.", untrusted_context=["mail"],
)
proposals.create_proposal(
    db, user.id, name="E2E hourly sweep", objective="Every hour, summarize new mail.",
    tool_ids=["core", "mail"], kind="schedule", schedule={"type": "interval", "minutes": 60},
    timezone="UTC", rationale="To keep you posted.",
)
`;

test.describe('Suggested automations (JAN-235)', () => {
  // The proposals, the review badge counts and the tool switch are the user's own, so a
  // throwaway user sees exactly these two proposals whatever other specs left on the admin
  // (JAN-336), and nothing needs resetting afterwards.
  test.use({ asThrowawayUser: true });

  test.beforeEach(async ({ account }) => {
    await runBackendPython(SEED_SCRIPT, [], { as: account.username });
  });

  test('reviews, approves with a blocked tool left unticked, and rejects with a reason', async ({ page }) => {
    await page.goto('/settings?tab=runtime');

    // The count shows on the Triggers section entry and the primary Settings entry.
    await expect(page.getByRole('tab', { name: /Triggers/ }).getByLabel('2 suggested automations awaiting review')).toBeVisible();
    await expect(page.getByLabel('2 suggested automations awaiting review').first()).toBeVisible();

    const section = page.getByRole('heading', { name: /Suggested automations/ });
    await expect(section).toBeVisible();

    const labels = page.getByRole('article', { name: 'E2E newsletter labels' });
    await expect(labels.getByTestId('proposal-objective')).toHaveText('Every morning, relabel newsletters as DIGEST.');
    await expect(labels.getByTestId('proposal-rationale')).toHaveText('You said newsletters clutter your inbox.');
    await expect(labels.getByTestId('proposal-provenance')).toHaveText('Drafted after reading email content');
    const allow = labels.getByRole('checkbox', { name: 'Allow Relabel mail' });
    await expect(allow).not.toBeChecked();

    // Approve without ticking the blocked tool: it is dropped from the definition.
    await labels.getByRole('button', { name: 'Approve', exact: true }).click();
    await expect(labels).toHaveCount(0);
    const approved = page.getByTestId(/^trigger-definition-\d+$/).filter({ hasText: 'E2E newsletter labels' });
    await expect(approved).toBeVisible();
    await expect(approved.getByText('Suggested by Janus, approved')).toBeVisible();
    await expect(approved.getByText('Tools: core, mail', { exact: true })).toBeVisible();

    // Reject the other one with a reason Janus will see.
    const sweep = page.getByRole('article', { name: 'E2E hourly sweep' });
    await sweep.getByRole('button', { name: 'Reject' }).click();
    await sweep.getByLabel(/Reason/).fill('Too noisy');
    await sweep.getByRole('button', { name: 'Confirm reject' }).click();
    await expect(sweep).toHaveCount(0);
    await expect(page.getByText('No suggestions await review.')).toBeVisible();
    await page.getByText('History', { exact: true }).click();
    await expect(page.getByText('“Too noisy”')).toBeVisible();

    await expect(page.getByLabel(/suggested automations? awaiting review/)).toHaveCount(0);
  });
});
