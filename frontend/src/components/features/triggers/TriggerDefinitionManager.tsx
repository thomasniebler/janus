import { formatServerDateTime, parseServerDate } from '../../../lib/serverTime';
import React, { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronUp, Pencil, Play, Plus, Trash2, XCircle } from 'lucide-react';
import { mcpApi, triggerDefinitionApi } from '../../../api';
import type {
  MCPBuiltin,
  TriggerDefinition,
  TriggerDefinitionInput,
  TriggerDefinitionKind,
  TriggerSubsystemSpec,
  TriggerSchedule,
} from '../../../types';
import { Badge } from '../../ui/Badge';
import { Button } from '../../ui/Button';
import { Card, CardContent, CardHeader } from '../../ui/Card';
import { Input } from '../../ui/Input';
import { Modal } from '../../ui/Modal';
import { ModelChoiceFields } from '../ModelChoiceFields';
import { useTriggerDefinitionsPageContext } from '../../../hooks/pageContextSections';
import { useConfirmDialog } from '../../../hooks/useConfirmDialog';
import { TriggerEventPicker } from './TriggerEventPicker';
import { TriggerRunLimitsFields } from './TriggerRunLimitsFields';
import { describeRunLimits } from './triggerRunLimits';
import { TriggerEventFilterEditor, TriggerEventOptionsSummary } from './TriggerEventFilterEditor';
import { useTriggerEventOptions } from './triggerEventFilters';
import { TriggerWidgetList } from './TriggerWidgetList';
import { describeCatalogEvent, firstCatalogEvent, triggerEventCatalogQueryKey } from './triggerEventCatalog';
import {
  browserTimezone,
  triggerDefinitionQueryKey,
  useRefreshTriggerDefinitions,
  useTriggerDefinitionActions,
  useTriggerDefinitions,
} from './triggerDefinitionQueries';

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const selectClass =
  'block w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-gray-700 rounded-xl outline-none text-sm';
const labelClass = 'text-xs font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider ml-1';

const emptyDraft = (): TriggerDefinitionInput => ({
  name: '',
  objective: '',
  tool_ids: ['core'],
  kind: 'manual',
  schedule: null,
  timezone: browserTimezone(),
  event_type: null,
  enabled: true,
  llm_provider: null,
  llm_model: null,
});

const draftFrom = (definition: TriggerDefinition): TriggerDefinitionInput => ({
  name: definition.name,
  objective: definition.objective,
  tool_ids: [...definition.tool_ids],
  kind: definition.kind,
  schedule: definition.schedule ? { ...definition.schedule } : null,
  timezone: definition.timezone,
  event_type: definition.event_type,
  event_filter: definition.event_filter ?? null, max_runs_per_hour: definition.max_runs_per_hour ?? null, // JAN-363
  enabled: definition.enabled,
  llm_provider: definition.llm_provider,
  llm_model: definition.llm_model,
  max_tool_calls: definition.max_tool_calls ?? null,
  max_run_seconds: definition.max_run_seconds ?? null,
});

const describeActivation = (
  definition: Pick<TriggerDefinition, 'kind' | 'schedule' | 'timezone' | 'event_type'>,
  eventCatalog: TriggerSubsystemSpec[],
): string => {
  if (definition.kind === 'event') return `On event: ${describeCatalogEvent(eventCatalog, definition.event_type)}`;
  if (definition.kind === 'manual') return 'Manual (run on demand)';
  const schedule = definition.schedule;
  if (!schedule) return 'Schedule (not configured)';
  if (schedule.type === 'once') return schedule.at ? `Once at ${formatUtcTimestamp(schedule.at)}` : 'Once';
  if (schedule.type === 'interval') return `Every ${schedule.minutes} minutes`;
  const days = schedule.days && schedule.days.length > 0 && schedule.days.length < 7
    ? schedule.days.map((day) => WEEKDAYS[day]).join(', ')
    : 'every day';
  return `Daily at ${schedule.time} (${days}, ${definition.timezone})`;
};

/** One of Janus's own one-time chat follow-ups (JAN-257): visible, cancellable, never editable. */
const isAgentFollowUp = (definition: Pick<TriggerDefinition, 'origin' | 'schedule'>): boolean =>
  definition.origin === 'agent' && definition.schedule?.type === 'once';

/** A follow-up whose moment has passed or that was cancelled; hidden by default so the list stays short. */
const isFinishedFollowUp = (definition: TriggerDefinition, now: number): boolean => {
  if (!isAgentFollowUp(definition)) return false;
  if (!definition.enabled) return true;
  const at = parseServerDate(definition.schedule?.at)?.getTime();
  return at !== undefined && at < now;
};

const originBadge = (definition: TriggerDefinition) => {
  if (definition.builtin_key) return <Badge variant="primary">Built-in</Badge>;
  if (definition.origin === 'agent' && definition.proposal_status === 'approved') return <Badge variant="warning">Suggested by Janus, approved</Badge>;
  if (definition.origin === 'agent') return <Badge variant="warning">By Janus</Badge>;
  return <Badge variant="gray">Yours</Badge>;
};

/** Backend timestamps are UTC, some without an explicit offset; shown in the viewer's local time. */
const formatUtcTimestamp = (value: string): string => formatServerDateTime(value) ?? value;

const errorDetail = (error: unknown, fallback: string): string => {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
};

interface EditorProps {
  initial: TriggerDefinitionInput;
  builtin: boolean;
  /** Whether the user wrote this definition, so picking an off tool allows it here. */
  userAuthored: boolean;
  tools: MCPBuiltin[];
  eventCatalog: TriggerSubsystemSpec[];
  isSaving: boolean;
  error: string | null;
  onCancel: () => void;
  onSave: (draft: TriggerDefinitionInput) => void;
  /** Submit button text; reviewing a Janus suggestion saves it as "Save and approve" (JAN-235). */
  submitLabel?: string;
}

export const DefinitionEditor: React.FC<EditorProps> = ({ initial, builtin, userAuthored, tools, eventCatalog, isSaving, error, onCancel, onSave, submitLabel = 'Save trigger' }) => {
  const [draft, setDraft] = useState<TriggerDefinitionInput>(initial);
  const eventOptions = useTriggerEventOptions(initial, draft, eventCatalog); // filter and run limit, JAN-363
  const update = <K extends keyof TriggerDefinitionInput>(key: K, value: TriggerDefinitionInput[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));
  const updateSchedule = (changes: Partial<TriggerSchedule>) =>
    setDraft((current) => ({
      ...current,
      schedule: { ...(current.schedule ?? { type: 'daily', time: '07:00', days: [] }), ...changes },
    }));
  const setKind = (kind: TriggerDefinitionKind) =>
    setDraft((current) => ({
      ...current,
      kind,
      schedule: kind === 'schedule' ? current.schedule ?? { type: 'daily', time: '07:00', days: [] } : null,
      event_type: kind === 'event' ? current.event_type ?? firstCatalogEvent(eventCatalog) : null,
    }));
  const toggleTool = (toolId: string, checked: boolean) =>
    setDraft((current) => ({
      ...current,
      tool_ids: checked ? [...current.tool_ids, toolId] : current.tool_ids.filter((id) => id !== toolId),
    }));
  // Locked capabilities (e.g. the not-owner-scoped system event log, JAN-195)
  // are never available to triggers and are not offered; one already on the
  // draft stays visible, marked, so it can be unchecked. A lock lifted by the owner's
  // "Unattended: Allow everything" mode (image generation, JAN-359) does not hide the tool.
  const isLocked = (tool: MCPBuiltin & { effective_unattended_allowed?: boolean }) =>
    !!tool.locked_reason && !tool.effective_unattended_allowed;
  const selectableTools = tools.filter((tool) => !isLocked(tool) || draft.tool_ids.includes(tool.id));
  const toggleDay = (day: number, checked: boolean) => {
    const days = new Set(draft.schedule?.days ?? []);
    if (checked) days.add(day);
    else days.delete(day);
    updateSchedule({ days: [...days].sort() });
  };

  return (
    <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); onSave(eventOptions.apply(draft)); }}>
      <Input label="Name" value={draft.name} onChange={(event) => update('name', event.target.value)} required />
      <div className="space-y-1.5">
        <label className={labelClass} htmlFor="trigger-definition-objective">Objective (prompt)</label>
        <textarea
          id="trigger-definition-objective"
          value={draft.objective}
          onChange={(event) => update('objective', event.target.value)}
          rows={6}
          required
          className="w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 resize-y"
        />
        <p className="text-xs text-gray-500 dark:text-gray-400">
          Janus always runs this as your Character with the full skill library; this text says what to do.
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-1.5">
          <label className={labelClass} htmlFor="trigger-definition-kind">Activation</label>
          <select
            id="trigger-definition-kind"
            value={draft.kind}
            disabled={builtin}
            onChange={(event) => setKind(event.target.value as TriggerDefinitionKind)}
            className={selectClass}
          >
            <option value="manual">Manual (run on demand)</option>
            <option value="schedule">Recurring schedule</option>
            <option value="event">Event</option>
          </select>
        </div>
        {draft.kind === 'event' && (
          <TriggerEventPicker
            catalog={eventCatalog}
            value={draft.event_type}
            onChange={(eventType) => update('event_type', eventType)}
          />
        )}
        {draft.kind === 'schedule' && (
          <div className="space-y-1.5">
            <label className={labelClass} htmlFor="trigger-definition-schedule-type">Repeat</label>
            <select
              id="trigger-definition-schedule-type"
              value={draft.schedule?.type ?? 'daily'}
              onChange={(event) =>
                updateSchedule(
                  event.target.value === 'interval'
                    ? { type: 'interval', minutes: draft.schedule?.minutes ?? 60, time: null, days: [] }
                    : { type: 'daily', time: draft.schedule?.time ?? '07:00', minutes: null }
                )
              }
              className={selectClass}
            >
              <option value="daily">Daily at a time</option>
              <option value="interval">Every N minutes</option>
            </select>
          </div>
        )}
      </div>
      <TriggerEventFilterEditor {...eventOptions.editorProps} />
      {draft.kind === 'schedule' && draft.schedule?.type === 'daily' && (
        <div className="grid gap-4 md:grid-cols-2">
          <Input
            label="Time"
            type="time"
            value={draft.schedule.time ?? '07:00'}
            onChange={(event) => updateSchedule({ time: event.target.value })}
            required
          />
          <Input label="Timezone" value={draft.timezone} onChange={(event) => update('timezone', event.target.value)} required />
          <fieldset className="md:col-span-2">
            <legend className={labelClass}>Days (none selected = every day)</legend>
            <div className="mt-2 flex flex-wrap gap-3">
              {WEEKDAYS.map((label, day) => (
                <label key={label} className="flex items-center gap-1.5 text-sm">
                  <input
                    type="checkbox"
                    checked={draft.schedule?.days?.includes(day) ?? false}
                    onChange={(event) => toggleDay(day, event.target.checked)}
                  />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
        </div>
      )}
      {draft.kind === 'schedule' && draft.schedule?.type === 'interval' && (
        <Input
          label="Interval (minutes, 5–10080)"
          type="number"
          min={5}
          max={10080}
          value={draft.schedule.minutes ?? 60}
          onChange={(event) => updateSchedule({ minutes: Number(event.target.value) })}
          required
        />
      )}
      <fieldset>
        <legend className={labelClass}>Tools this trigger may use</legend>
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
          Only the selected tools are offered to Janus.{' '}
          {userAuthored
            ? 'On a trigger you create, picking a tool that is off for unattended runs allows it for this trigger.'
            : 'Tools that are off for unattended runs (Settings → Tools & Connections) stay refused here.'}
        </p>
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          {selectableTools.map((tool) => (
            <label key={tool.id} className="flex min-w-0 items-start gap-2 rounded-lg border border-gray-200 dark:border-gray-800 p-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={draft.tool_ids.includes(tool.id)}
                onChange={(event) => toggleTool(tool.id, event.target.checked)}
              />
              <span className="min-w-0">
                <span className="block font-medium">
                  {tool.display_name}{' '}
                  {!tool.locked_reason && tool.unattended_allowed === false && (
                    <span className="text-[10px] uppercase text-amber-500">
                      {userAuthored ? 'off for unattended runs; allowed here if picked' : 'off for unattended runs'}
                    </span>
                  )}
                  {isLocked(tool) && (
                    <span className="text-[10px] uppercase text-red-500">not available to triggers</span>
                  )}
                </span>
                <span className="block break-words text-xs text-gray-500 dark:text-gray-400">{tool.description}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <ModelChoiceFields
        label="Trigger"
        provider={draft.llm_provider}
        model={draft.llm_model}
        onChange={(provider, model) => setDraft((current) => ({ ...current, llm_provider: provider, llm_model: model }))}
        dataDescription="this trigger's objective, its event data (e.g. email content), and every tool result"
      />
      <TriggerRunLimitsFields limits={draft} onChange={update} />
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={draft.enabled ?? true} onChange={(event) => update('enabled', event.target.checked)} />
        Enabled
      </label>
      {error && <p className="text-sm text-red-500">{error}</p>}
      <div className="flex gap-2">
        <Button type="submit" size="sm" isLoading={isSaving}>{submitLabel}</Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
};

const DefinitionRuns: React.FC<{ definitionId: number }> = ({ definitionId }) => {
  const { data: runs = [], isLoading } = useQuery({
    queryKey: [...triggerDefinitionQueryKey, definitionId, 'runs'],
    queryFn: async () => (await triggerDefinitionApi.runs(definitionId)).data,
    refetchInterval: 5000,
  });
  if (isLoading) return <p className="text-xs text-gray-500">Loading runs…</p>;
  if (runs.length === 0) return <p className="text-xs text-gray-500">No runs yet.</p>;
  return <TriggerWidgetList triggers={runs} widgetStyle="card" />;
};

export interface TriggerDefinitionManagerProps {
  /** Definition to scroll to and highlight, e.g. after clicking a run's definition link (JAN-256). */
  highlightedId?: number | null;
}

export const TriggerDefinitionManager: React.FC<TriggerDefinitionManagerProps> = ({ highlightedId = null }) => {
  const { confirm, dialog } = useConfirmDialog();
  const [editing, setEditing] = useState<{
    id: number | null;
    builtin: boolean;
    userAuthored: boolean;
    draft: TriggerDefinitionInput;
  } | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [showFinished, setShowFinished] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: definitions = [], isLoading, isError, dataUpdatedAt } = useTriggerDefinitions();
  const { data: registry } = useQuery({ queryKey: ['mcp-registry'], queryFn: async () => (await mcpApi.getRegistry()).data });
  const { data: eventCatalog = [] } = useQuery({
    queryKey: triggerEventCatalogQueryKey,
    queryFn: async () => (await triggerDefinitionApi.eventCatalog()).data,
  });
  useEffect(() => {
    if (highlightedId === null) return;
    const element = document.getElementById(`trigger-definition-${highlightedId}`);
    element?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
  }, [highlightedId, definitions.length, showFinished]);
  // "Finished" is judged as of the last fetch, which refreshes every 15 s.
  const now = dataUpdatedAt;
  const finishedCount = definitions.filter((definition) => isFinishedFollowUp(definition, now)).length;
  const visibleDefinitions = definitions.filter(
    (definition) => showFinished || definition.id === highlightedId || !isFinishedFollowUp(definition, now)
  );
  useTriggerDefinitionsPageContext(visibleDefinitions, editing?.id ?? expanded, showFinished, { expanded: [expanded, setExpanded], setShowFinished }); // what Janus sees and may drive in chat (JAN-250, JAN-251)
  const refresh = useRefreshTriggerDefinitions();
  const saveMutation = useMutation({
    mutationFn: ({ id, draft }: { id: number | null; draft: TriggerDefinitionInput }) =>
      id === null ? triggerDefinitionApi.create(draft) : triggerDefinitionApi.update(id, draft),
    onSuccess: () => {
      setEditing(null);
      setError(null);
      refresh();
    },
    onError: (err) => setError(errorDetail(err, 'Could not save the trigger.')),
  });
  const { toggle: toggleMutation, run: runMutation, remove: deleteMutation } = useTriggerDefinitionActions(setExpanded);

  return (
    <Card data-ui-section="trigger_definitions">
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="font-bold">Trigger definitions</h3>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Everything Janus does outside chat runs from here: an objective, the tools it may use, and when it runs.
            </p>
          </div>
          <Button size="sm" icon={<Plus size={15} />} onClick={() => { setError(null); setEditing({ id: null, builtin: false, userAuthored: true, draft: emptyDraft() }); }}>
            New trigger
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading && <p className="text-sm text-gray-500">Loading triggers…</p>}
        {isError && <p className="text-sm text-red-400">Trigger definitions are unavailable. Apply the backend migration first.</p>}
        {!isLoading && !isError && definitions.length === 0 && <p className="text-sm text-gray-500 dark:text-gray-400">No trigger definitions yet.</p>}
        {visibleDefinitions.map((definition) => {
          const isExpanded = expanded === definition.id;
          const followUp = isAgentFollowUp(definition);
          const highlighted = definition.id === highlightedId;
          return (
            <div
              key={definition.id}
              id={`trigger-definition-${definition.id}`} data-ui-item={`trigger_definition:${definition.id}`}
              className={`rounded-xl border p-4 transition-shadow ${highlighted ? 'border-primary ring-2 ring-primary/60' : 'border-gray-200 dark:border-gray-800'}`}
              data-testid={`trigger-definition-${definition.id}`}
              data-highlighted={highlighted || undefined}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h4 className="font-semibold">{definition.name}</h4>
                    {originBadge(definition)}
                    {followUp && <Badge variant="gray">One-time follow-up</Badge>}
                    {!definition.enabled && <Badge variant="gray">{followUp ? 'Cancelled' : 'Disabled'}</Badge>}
                  </div>
                  <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    {describeActivation(definition, eventCatalog)}
                    {definition.next_run_at && ` · next run ${formatUtcTimestamp(definition.next_run_at)}`}
                  </p>
                  <TriggerEventOptionsSummary definition={definition} />
                  {followUp ? (
                    <>
                      <p className="mt-1 whitespace-pre-wrap break-words text-xs text-gray-700 dark:text-gray-300" data-testid="follow-up-objective">
                        {definition.objective}
                      </p>
                      <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                        Scheduled by Janus from chat. Runs once with the restricted trigger toolset; nothing it uses counts as confirmed.
                      </p>
                    </>
                  ) : (
                  <>
                  <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Tools: {definition.tool_ids.join(', ') || 'none'}</p>
                  <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    Model:{' '}
                    {definition.llm_provider || definition.llm_model
                      ? `${definition.llm_provider ?? 'default provider'}/${definition.llm_model ?? 'configured model'}`
                      : 'default'}
                    {definition.llm_provider && definition.llm_provider !== 'ollama' && (
                      <span className="ml-2 text-[10px] uppercase text-amber-500">cloud</span>
                    )}
                  </p>
                  <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Limits: {describeRunLimits(definition)}</p>
                  </>
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  {followUp ? (
                    definition.enabled && (
                      <Button size="sm" variant="outline" icon={<XCircle size={14} />} isLoading={toggleMutation.isPending && toggleMutation.variables?.id === definition.id} onClick={() => toggleMutation.mutate(definition)}>
                        Cancel follow-up
                      </Button>
                    )
                  ) : (
                  <>
                  <label className="flex items-center gap-1.5 text-xs">
                    <input type="checkbox" checked={definition.enabled} onChange={() => toggleMutation.mutate(definition)} aria-label={`Enable ${definition.name}`} />
                    Enabled
                  </label>
                  <Button size="sm" variant="outline" icon={<Play size={14} />} isLoading={runMutation.isPending && runMutation.variables === definition.id} onClick={() => runMutation.mutate(definition.id)}>
                    Run now
                  </Button>
                  <Button size="sm" variant="outline" icon={<Pencil size={14} />} onClick={() => { setError(null); setEditing({ id: definition.id, builtin: definition.builtin_key !== null, userAuthored: definition.origin === 'user' || definition.proposal_status === 'approved', draft: draftFrom(definition) }); }}>
                    Edit
                  </Button>
                  </>
                  )}
                  {definition.builtin_key === null && (
                    <Button size="sm" variant="danger" icon={<Trash2 size={14} />} aria-label={`Delete ${definition.name}`} onClick={() => confirm({ title: 'Delete trigger?', message: `Delete the trigger "${definition.name}" and stop its future runs?`, confirmLabel: 'Delete', tone: 'warning', onConfirm: () => deleteMutation.mutate(definition.id) })} />
                  )}
                  <Button size="sm" variant="ghost" icon={isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />} onClick={() => setExpanded(isExpanded ? null : definition.id)}>
                    Runs
                  </Button>
                </div>
              </div>
              {isExpanded && (
                <div className="mt-3 border-t border-gray-200 dark:border-gray-800 pt-3">
                  <DefinitionRuns definitionId={definition.id} />
                </div>
              )}
            </div>
          );
        })}
        {finishedCount > 0 && (
          <button
            type="button"
            onClick={() => setShowFinished((value) => !value)}
            className="text-xs font-medium text-primary hover:underline"
          >
            {showFinished ? 'Hide finished Janus follow-ups' : `Show ${finishedCount} finished Janus follow-up${finishedCount === 1 ? '' : 's'}`}
          </button>
        )}
      </CardContent>
      <Modal isOpen={editing !== null} onClose={() => setEditing(null)} title={editing?.id === null ? 'New trigger' : 'Edit trigger'} className="max-w-3xl">
        {editing && (
          <DefinitionEditor
            key={editing.id ?? 'new'}
            initial={editing.draft}
            builtin={editing.builtin}
            userAuthored={editing.userAuthored}
            tools={registry?.builtins ?? []}
            eventCatalog={eventCatalog}
            isSaving={saveMutation.isPending}
            error={error}
            onCancel={() => setEditing(null)}
            onSave={(draft) => saveMutation.mutate({ id: editing.id, draft })}
          />
        )}
      </Modal>
      {dialog}
    </Card>
  );
};
