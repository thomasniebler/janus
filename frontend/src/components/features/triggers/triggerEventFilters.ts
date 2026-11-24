import { useState } from 'react';
import type { TriggerDefinitionInput, TriggerEventSpec, TriggerSubsystemSpec, WhatsAppConversationSummary } from '../../../types';
import { findCatalogEvent } from './triggerEventCatalog';

/**
 * Event filter and run-limit helpers for event-activated trigger definitions (JAN-363):
 * the editor's draft state, what it sends to the API, and the cards' summary text.
 */

export type TriggerEventCondition = NonNullable<TriggerDefinitionInput['event_filter']>[number];
export type TriggerEventConditionOp = TriggerEventCondition['op'];

/** The backend accepts at most this many conditions per definition. */
export const MAX_EVENT_CONDITIONS = 10;

export const OP_LABELS: Record<TriggerEventConditionOp, string> = {
  in: 'is one of',
  not_in: 'is not one of',
  contains: 'contains',
};

/** The backend accepts at most this many values per condition, each at most this long. */
export const MAX_CONDITION_VALUES = 50;
export const MAX_CONDITION_VALUE_LENGTH = 200;

/**
 * A condition as the editor holds it. Values are kept one by one, never as
 * joined text, so a value containing a comma round-trips unchanged; `key` is a
 * stable React key that survives removing an earlier condition.
 */
export interface EventConditionDraft {
  key: number;
  field: string;
  op: TriggerEventConditionOp;
  values: string[];
}

let nextConditionKey = 0;

/** A new condition draft with a fresh key. */
export const newConditionDraft = (field: string, op: TriggerEventConditionOp = 'in', values: string[] = []): EventConditionDraft => ({
  key: nextConditionKey++,
  field,
  op,
  values,
});

const conditionDrafts = (filter: TriggerEventCondition[] | null | undefined): EventConditionDraft[] =>
  (filter ?? []).map((condition) => newConditionDraft(condition.field, condition.op, [...condition.values]));

/** What the API takes: `null` when there are no conditions. */
const eventFilterFromDrafts = (drafts: EventConditionDraft[]): TriggerEventCondition[] | null =>
  drafts.length === 0 ? null : drafts.map((draft) => ({ field: draft.field, op: draft.op, values: draft.values }));

/** Empty text means "no limit". */
const maxRunsFromText = (text: string): number | null => (text.trim() === '' ? null : Number(text));

/** "chat_id is one of a, b" for each condition. */
export const describeEventFilter = (filter: TriggerEventCondition[] | null | undefined): string[] =>
  (filter ?? []).map((condition) => `${condition.field} ${OP_LABELS[condition.op] ?? condition.op} ${condition.values.join(', ')}`);

/** Payload fields whose values the user's stored WhatsApp conversations can suggest. */
export const SUGGESTED_FIELDS = new Set(['chat_id', 'chat', 'sender_id', 'sender']);

/** Suggested values for a WhatsApp event's `field`, from the user's stored conversations. */
export const conversationSuggestions = (
  eventType: string,
  field: string,
  conversations: WhatsAppConversationSummary[],
): { value: string; label: string }[] => {
  const chatType = eventType === 'whatsapp.direct_message_received' ? 'direct' : 'group';
  // A direct chat's id and title are that person's, so they double as sender suggestions.
  const source = field.startsWith('chat') ? chatType : 'direct';
  const byId = field.endsWith('_id');
  return conversations
    .filter((conversation) => conversation.conversation_type === source)
    .map((conversation) => ({
      value: byId ? conversation.provider_chat_id : conversation.title,
      label: byId ? conversation.title : conversation.provider_chat_id,
    }));
};

/**
 * Editor state for an event definition's filter and limit.
 *
 * A definition that was not event-activated yet (a new one) shows the selected
 * event's default limit until the user types their own; an existing event
 * definition shows its stored values. Conditions on fields the selected event
 * does not carry are dropped, since they could never match.
 */
export const useTriggerEventOptions = (
  initial: TriggerDefinitionInput,
  draft: Pick<TriggerDefinitionInput, 'kind' | 'event_type'>,
  eventCatalog: TriggerSubsystemSpec[],
) => {
  const [allConditions, setConditions] = useState<EventConditionDraft[]>(() => conditionDrafts(initial.event_filter));
  const [typedMaxRuns, setTypedMaxRuns] = useState<string | null>(() =>
    initial.kind === 'event' ? String(initial.max_runs_per_hour ?? '') : null
  );
  const event: TriggerEventSpec | null =
    draft.kind === 'event' ? findCatalogEvent(eventCatalog, draft.event_type)?.event ?? null : null;
  const fields = new Set(event?.payload.map((field) => field.name) ?? []);
  const conditions = event ? allConditions.filter((condition) => fields.has(condition.field)) : allConditions;
  const maxRuns = typedMaxRuns ?? String(event?.default_max_runs_per_hour ?? '');

  /** The draft as the API takes it: event options only on event definitions. */
  const apply = (value: TriggerDefinitionInput): TriggerDefinitionInput => {
    const rest = { ...value };
    delete rest.event_filter;
    delete rest.max_runs_per_hour;
    if (value.kind !== 'event') return rest;
    return { ...rest, event_filter: eventFilterFromDrafts(conditions), max_runs_per_hour: maxRunsFromText(maxRuns) };
  };

  return {
    apply,
    editorProps: {
      event: draft.kind === 'event' && eventCatalog.length > 0 ? event : undefined,
      conditions,
      onConditionsChange: setConditions,
      maxRuns,
      onMaxRunsChange: setTypedMaxRuns,
    },
  };
};
