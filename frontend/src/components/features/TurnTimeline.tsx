import React, { useContext, useId, useState } from 'react';
import { Ban, CheckCircle2, ChevronDown, ChevronRight, Circle, Hourglass, ListChecks, Loader2, XCircle } from 'lucide-react';

import { useChatSession } from '../../contexts/chatSession';
import { TurnTimelineDockContext } from './turnTimelineDock';
import { ToolsetSelectionNote } from './ToolsetSelectionNote';
import {
  buildTimeline,
  STEP_STATUS_LABELS,
  type PlanStepDisplayStatus,
  type PlanStepRecord,
  type TimelineStep,
} from '../../lib/planSteps';
import { isAwaitingApproval } from '../../lib/toolCallEvents';
import type { ToolCallRecord } from '../../types';

const STEP_ICONS: Record<PlanStepDisplayStatus, React.ReactNode> = {
  pending: <Circle size={13} className="text-gray-400" aria-hidden="true" />,
  running: <Loader2 size={13} className="animate-spin text-primary" aria-hidden="true" />,
  waiting: <Hourglass size={13} className="text-amber-600 dark:text-amber-400" aria-hidden="true" />,
  completed: <CheckCircle2 size={13} className="text-emerald-600 dark:text-emerald-400" aria-hidden="true" />,
  failed: <XCircle size={13} className="text-red-600 dark:text-red-400" aria-hidden="true" />,
};

type CallState = 'planned' | 'running' | 'waiting' | 'done' | 'failed' | 'blocked';

const CALL_STYLES: Record<CallState, { icon: React.ReactNode; chipClass: string }> = {
  planned: {
    icon: <Circle size={10} className="text-gray-400" aria-hidden="true" />,
    chipClass: 'border-dashed border-gray-300 text-gray-500 dark:border-gray-600 dark:text-gray-400',
  },
  running: {
    icon: <Loader2 size={10} className="animate-spin" aria-hidden="true" />,
    chipClass: 'border-gray-300/80 text-gray-600 dark:border-gray-600 dark:text-gray-300',
  },
  waiting: {
    icon: <Hourglass size={10} className="text-amber-600 dark:text-amber-400" aria-hidden="true" />,
    chipClass: 'border-amber-300/80 text-amber-800 dark:border-amber-800 dark:text-amber-300',
  },
  done: {
    icon: <CheckCircle2 size={10} className="text-emerald-600 dark:text-emerald-400" aria-hidden="true" />,
    chipClass: 'border-gray-300/80 text-gray-700 dark:border-gray-600 dark:text-gray-200',
  },
  failed: {
    icon: <XCircle size={10} className="text-red-600 dark:text-red-400" aria-hidden="true" />,
    chipClass: 'border-red-300/80 text-red-700 dark:border-red-800 dark:text-red-300',
  },
  blocked: {
    icon: <Ban size={10} className="text-amber-600 dark:text-amber-400" aria-hidden="true" />,
    chipClass: 'border-amber-300/80 text-amber-800 dark:border-amber-800 dark:text-amber-300',
  },
};

const callState = (call: ToolCallRecord): CallState => {
  if (isAwaitingApproval(call)) return 'waiting';
  if (call.phase === 'start') return 'running';
  if (call.phase === 'end') return 'done';
  return call.phase === 'error' ? 'failed' : 'blocked';
};

/** The tools the model asked for in a step that have not started yet (by name, counting repeats). */
const plannedNotStarted = (step: PlanStepRecord, calls: ToolCallRecord[]): string[] => {
  const started = calls.map((call) => call.tool_name);
  return step.tool_names.filter((name) => {
    const at = started.indexOf(name);
    if (at === -1) return true;
    started.splice(at, 1);
    return false;
  });
};

const Chip: React.FC<{ name: string; state: CallState }> = ({ name, state }) => (
  <li
    className={`inline-flex items-center gap-1 rounded-full border bg-white/80 px-1.5 py-0.5 text-[11px] dark:bg-gray-900/70 ${CALL_STYLES[state].chipClass}`}
    aria-label={`${name}, ${state === 'waiting' ? 'waiting for approval' : state}`}
  >
    {CALL_STYLES[state].icon}
    <span className="max-w-[12rem] truncate font-mono" aria-hidden="true">
      {name}
    </span>
  </li>
);

const StepItem: React.FC<{ entry: TimelineStep }> = ({ entry }) => {
  const { step, status, calls } = entry;
  const planned = plannedNotStarted(step, calls);
  return (
    <li className="space-y-1" data-testid="turn-timeline-step">
      <div className="flex items-center gap-1.5 text-xs">
        {STEP_ICONS[status]}
        <span className="font-medium text-gray-700 dark:text-gray-200">Step {step.index}</span>
        <span className="text-gray-500 dark:text-gray-400">· {STEP_STATUS_LABELS[status]}</span>
      </div>
      {(calls.length > 0 || planned.length > 0) && (
        <ul className="ml-5 flex flex-wrap gap-1" aria-label={`Step ${step.index} tool calls`}>
          {calls.map((call) => (
            <Chip key={call.call_id} name={call.tool_name} state={callState(call)} />
          ))}
          {planned.map((name, position) => (
            <Chip key={`planned-${position}-${name}`} name={name} state="planned" />
          ))}
        </ul>
      )}
    </li>
  );
};

/** One line naming where the turn is: the step in progress, or the last one. */
const summarize = (steps: TimelineStep[], isChatting: boolean): string => {
  if (steps.length === 0) return isChatting ? 'No steps yet' : 'No steps';
  const current = [...steps].reverse().find((entry) => entry.status !== 'completed' && entry.status !== 'failed') ?? steps[steps.length - 1];
  const count = `${steps.length} ${steps.length === 1 ? 'step' : 'steps'}`;
  return `${count} · step ${current.step.index} ${STEP_STATUS_LABELS[current.status]}`;
};

interface TurnTimelineViewProps {
  steps: PlanStepRecord[] | undefined;
  calls: ToolCallRecord[] | undefined;
  isChatting: boolean;
  /** Shows a toggle; the sidebar chat renders the timeline collapsed by default. */
  collapsible?: boolean;
  defaultOpen?: boolean;
}

/**
 * The plan/step timeline of the current chat turn (JAN-316): each model round
 * that asked for tools is a step (generated, running, waiting for approval,
 * completed, failed) with its tool calls as chips. Live only: steps come from
 * `__PLAN_EVENT__` frames and are not stored, so a reloaded conversation shows
 * none. Tool details and approval buttons stay in the transcript
 * (`ToolCallInspector`); this panel only shows progress.
 */
export const TurnTimelineView: React.FC<TurnTimelineViewProps> = ({
  steps,
  calls,
  isChatting,
  collapsible = false,
  defaultOpen = true,
}) => {
  const [open, setOpen] = useState(defaultOpen || !collapsible);
  const bodyId = useId();
  const timeline = buildTimeline(steps, steps?.length ? calls : undefined);
  const summary = summarize(timeline.steps, isChatting);
  const expanded = open || !collapsible;

  const heading = (
    <span className="flex min-w-0 items-center gap-1.5">
      <ListChecks size={14} className="shrink-0 text-primary" aria-hidden="true" />
      <span className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">This turn</span>
      <span className="truncate text-[11px] text-gray-500 dark:text-gray-400">{summary}</span>
    </span>
  );

  return (
    <section
      aria-label="Turn timeline"
      data-testid="turn-timeline"
      className="rounded-xl border border-gray-200 bg-white/60 p-2.5 dark:border-gray-800 dark:bg-gray-900/40"
    >
      {collapsible ? (
        <button
          type="button"
          className="flex w-full items-center justify-between gap-2 text-left"
          aria-expanded={expanded}
          aria-controls={bodyId}
          onClick={() => setOpen(!open)}
        >
          {heading}
          {expanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
        </button>
      ) : (
        <h2 className="flex items-center">{heading}</h2>
      )}
      {/* Announced even while collapsed, outside the toggle button. */}
      <span className="sr-only" role="status" aria-live="polite">
        {`Turn timeline: ${summary}`}
      </span>
      {expanded && (
        <div id={bodyId} className="mt-2 space-y-2">
          {timeline.steps.length === 0 ? (
            <p className="text-xs italic text-gray-500 dark:text-gray-400">
              {isChatting
                ? 'Janus has not called any tools in this reply yet.'
                : "The steps of Janus's current reply show here while it works. They are not kept after a reload."}
            </p>
          ) : (
            <ol className="space-y-2" aria-label="Steps">
              {timeline.steps.map((entry) => (
                <StepItem key={entry.step.step_id} entry={entry} />
              ))}
            </ol>
          )}
          {timeline.ungrouped.length > 0 && (
            <ul className="flex flex-wrap gap-1" aria-label="Other tool calls">
              {timeline.ungrouped.map((call) => (
                <Chip key={call.call_id} name={call.tool_name} state={callState(call)} />
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
};

/** The timeline of the session's latest reply (the reply streaming now, or the last one). */
export const TurnTimeline: React.FC<Pick<TurnTimelineViewProps, 'collapsible' | 'defaultOpen'>> = (props) => {
  const { messages, isChatting } = useChatSession();
  const last = [...messages].reverse().find((message) => message.role === 'assistant');
  return <TurnTimelineView steps={last?.planSteps} calls={last?.toolCalls} isChatting={isChatting} {...props} />;
};

/**
 * The turn's status above a chat view: the tools withheld this turn (from
 * `__TOOL_SELECTION__`), the tools Janus picked for it and why (JAN-300), and the collapsed timeline, which is left out where the
 * view docks its own (`/chat` at `xl`).
 */
export const ChatTurnStatus: React.FC = () => {
  const { toolSelection } = useChatSession();
  const docked = useContext(TurnTimelineDockContext);
  return (
    <>
      {toolSelection && toolSelection.denied.length > 0 && (
        <div
          role="status"
          className="max-h-16 overflow-y-auto rounded-md border border-gray-200/80 bg-gray-100/70 px-2.5 py-1.5 text-[11px] text-gray-600 dark:border-gray-700 dark:bg-gray-800/70 dark:text-gray-300"
        >
          <span className="font-medium">Tools unavailable this turn:</span>{' '}
          {toolSelection.denied.map((denial) => `${denial.tool_id} (${denial.reason.replace(/_/g, ' ')})`).join(', ')}
        </div>
      )}
      <ToolsetSelectionNote toolset={toolSelection?.toolset} />
      {!docked && <TurnTimeline collapsible defaultOpen={false} />}
    </>
  );
};
