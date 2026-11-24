import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { expect, userEvent, within } from 'storybook/test';
import { withQueryData } from '../../../test/storybookQueryClient';
import type { TriggerEventSpec, WhatsAppConversationSummary } from '../../../types';
import { TriggerEventFilterEditor } from './TriggerEventFilterEditor';
import { MAX_EVENT_CONDITIONS, newConditionDraft } from './triggerEventFilters';
import { triggerEventCatalogSample } from './triggerFixtures';

const mailEvent: TriggerEventSpec = triggerEventCatalogSample[0].events[0];
const groupEvent: TriggerEventSpec = triggerEventCatalogSample[1].events[0];

const conversations: WhatsAppConversationSummary[] = [
  { id: 1, whatsapp_account_id: 1, provider_chat_id: '491701234567-1600000000@g.us', title: 'Family, friends', conversation_type: 'group', unread_count: 0 },
  { id: 2, whatsapp_account_id: 1, provider_chat_id: '491709876543-1610000000@g.us', title: 'Climbing club', conversation_type: 'group', unread_count: 2 },
  { id: 3, whatsapp_account_id: 1, provider_chat_id: '4917012345@s.whatsapp.net', title: 'Ada', conversation_type: 'direct', unread_count: 0 },
];

const meta = {
  title: 'Features/Runtime triggers/TriggerEventFilterEditor',
  component: TriggerEventFilterEditor,
  decorators: [
    withQueryData((queryClient) => {
      queryClient.setQueryData(['whatsapp-conversations', 'trigger-filter'], conversations);
    }),
  ],
  args: {
    event: groupEvent,
    conditions: [],
    onConditionsChange: () => undefined,
    maxRuns: '10',
    onMaxRunsChange: () => undefined,
  },
  // The editor is controlled; the story keeps its state so chips and the limit can be edited.
  render: function Render(args) {
    const [conditions, setConditions] = useState(args.conditions);
    const [maxRuns, setMaxRuns] = useState(args.maxRuns);
    return (
      <div className="max-w-3xl">
        <TriggerEventFilterEditor
          {...args}
          conditions={conditions}
          onConditionsChange={(next) => {
            setConditions(next);
            args.onConditionsChange(next);
          }}
          maxRuns={maxRuns}
          onMaxRunsChange={(next) => {
            setMaxRuns(next);
            args.onMaxRunsChange(next);
          }}
        />
      </div>
    );
  },
} satisfies Meta<typeof TriggerEventFilterEditor>;

export default meta;

type Story = StoryObj<typeof meta>;

/** A new WhatsApp group trigger: no conditions yet, the event's default limit prefilled. */
export const Empty: Story = {};

/** WhatsApp conditions; the value inputs suggest the stored group chats, and a title with a comma stays one value. */
export const WhatsAppWithSuggestions: Story = {
  args: {
    conditions: [
      newConditionDraft('chat_id', 'in', ['491701234567-1600000000@g.us']),
      newConditionDraft('chat', 'not_in', ['Family, friends']),
    ],
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const input = canvas.getByLabelText('Values of condition 1');
    await userEvent.type(input, '491709876543-1610000000@g.us{Enter}');
    await expect(canvas.getByRole('button', { name: 'Remove 491709876543-1610000000@g.us' })).toBeInTheDocument();
    await expect(canvas.getByRole('button', { name: 'Remove Family, friends' })).toBeInTheDocument();
  },
};

/** A mail trigger with several conditions; mail has no default limit. */
export const MailSeveralConditions: Story = {
  args: {
    event: mailEvent,
    maxRuns: '',
    conditions: [
      newConditionDraft('from', 'in', ['boss@example.com', 'cto@example.com']),
      newConditionDraft('subject', 'contains', ['urgent', 'invoice']),
      newConditionDraft('body', 'not_in', ['unsubscribe']),
    ],
  },
};

/** The limit field emptied: every matching event starts a run. */
export const NoLimit: Story = {
  args: {
    maxRuns: '',
    conditions: [newConditionDraft('body', 'contains', ['dinner'])],
  },
};

/** The backend's condition cap is reached, so "Add condition" is disabled. */
export const MaxConditions: Story = {
  args: {
    event: mailEvent,
    maxRuns: '',
    conditions: Array.from({ length: MAX_EVENT_CONDITIONS }, (_, i) => newConditionDraft('subject', 'contains', [`term ${i + 1}`])),
  },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByRole('button', { name: 'Add condition' })).toBeDisabled();
  },
};

/** An uncatalogued event: no payload fields to filter on. */
export const UncataloguedEvent: Story = {
  args: { event: null, maxRuns: '' },
};
