import { runBackendPython } from './backendSeed';
import { expect, test } from './fixtures';

// JAN-407: skill suggestions. Three seeded chat turns (written by the real models, as the chat
// handler would) used the same tools in the same order. Settings -> Skills offers that routine;
// "Draft skill" opens the skill editor with the designer's goal prefilled (nothing is saved),
// and "Not now" hides it. Every test runs as its own throwaway user.

test.use({ asThrowawayUser: true });

const SEED_TURNS = `
from janus import models
for index in range(3):
    conversation = models.Conversation(user_id=user.id, title=f"E2E routine {index}")
    db.add(conversation)
    db.flush()
    message = models.ConversationMessage(conversation_id=conversation.id, role=models.MessageRole.ASSISTANT, content="done")
    db.add(message)
    db.flush()
    for position, name in enumerate(["e2e_search_mail", "e2e_create_task"]):
        db.add(models.ConversationToolCall(message_id=message.id, position=position, call_id=f"c{position}", tool_name=name, phase="done"))
db.commit()
print("ok")
`;

test('a repeated tool routine is offered as a skill, drafted, and dismissed', async ({ page, account }) => {
  await runBackendPython(SEED_TURNS, [], { as: account.username });
  await page.goto('/settings?tab=skills');

  const card = page.getByTestId('skill-suggestions');
  await expect(card).toContainText('e2e_search_mail → e2e_create_task');
  await expect(card).toContainText('In 3 chats');

  await card.getByRole('button', { name: 'Draft skill' }).click();
  const goal = page.getByLabel('What should this skill do?');
  await expect(goal).toHaveValue(/e2e_search_mail -> e2e_create_task/);
  await page.keyboard.press('Escape');

  await card.getByRole('button', { name: 'Not now' }).click();
  await expect(page.getByTestId('skill-suggestions')).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Skills' })).toBeVisible();
  await expect(page.getByTestId('skill-suggestions')).toHaveCount(0);
});
