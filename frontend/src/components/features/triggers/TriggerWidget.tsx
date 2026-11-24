import React from 'react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

import type { RuntimeTrigger } from '../../../types';
import {
  KIND_PRESENTATION,
  STATUS_PRESENTATION,
  formatAttempts,
  formatDuration,
  formatShortTimestamp,
  formatTimestamp,
  lifecycleSteps,
  triggerFailureDetail,
  triggerResultDetail,
  triggerResultHeadline,
  triggerRunTitle,
} from './triggerPresentation';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * The visual styles explored in Storybook for JAN-187.
 *
 * - `row` — a dense ledger line, optimised for scanning a long history.
 * - `card` — a dashboard tile with a status rail and a metadata grid.
 * - `timeline` — a monospace lifecycle trace, closest to a log reader.
 */
export type TriggerWidgetStyle = 'row' | 'card' | 'timeline';

export interface TriggerWidgetProps {
  trigger: RuntimeTrigger;
  /** Which explored style to render. Defaults to the recommended `card`. */
  widgetStyle?: TriggerWidgetStyle;
  /** Called with the run's definition id when its name is clicked; omit to render plain text. */
  onSelectDefinition?: (definitionId: number) => void;
  /** Extra links shown under the widget, e.g. to the run's plan steps on Agent Tasks (JAN-204). */
  actions?: React.ReactNode;
  className?: string;
}

interface RendererProps {
  trigger: RuntimeTrigger;
  onSelectDefinition?: (definitionId: number) => void;
}

/** The run's name, as a link to its definition when one exists and a handler is given. */
const RunTitle: React.FC<RendererProps & { className?: string }> = ({ trigger, onSelectDefinition, className }) => {
  const title = triggerRunTitle(trigger);
  const definitionId = trigger.definition_id;
  if (onSelectDefinition && definitionId != null && trigger.definition_name) {
    return (
      <button
        type="button"
        data-testid="trigger-definition-link"
        onClick={() => onSelectDefinition(definitionId)}
        title={`Show the “${title}” definition`}
        className={cn('max-w-full truncate text-left underline-offset-2 hover:underline focus-visible:underline', className)}
      >
        {title}
      </button>
    );
  }
  return <span className={cn('truncate', className)}>{title}</span>;
};

const MONO = 'font-mono uiv2:font-terminal uiv3:font-typewriter';
const MUTED = 'text-gray-500 dark:text-gray-400 uiv2:text-v2-text-muted uiv3:text-v3-text-muted';
const STRONG = 'text-gray-900 dark:text-gray-100 uiv2:text-v2-text uiv3:text-v3-text';
const SURFACE = 'border-gray-200 dark:border-gray-700 uiv2:border-v2-border uiv3:border-v3-border';

const StatusBadge: React.FC<{ trigger: RuntimeTrigger; compact?: boolean }> = ({ trigger, compact }) => {
  const status = STATUS_PRESENTATION[trigger.status];
  const Icon = status.icon;
  return (
    <span
      data-testid="trigger-status-badge"
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border font-bold uppercase tracking-wider',
        compact ? 'px-2 py-0.5 text-[10px]' : 'px-2.5 py-1 text-[11px]',
        status.badgeClass
      )}
    >
      <Icon size={compact ? 11 : 13} className={status.inFlight ? 'animate-spin' : undefined} aria-hidden />
      {status.label}
    </span>
  );
};

const KindBadge: React.FC<{ trigger: RuntimeTrigger }> = ({ trigger }) => {
  const kind = KIND_PRESENTATION[trigger.kind];
  const Icon = kind.icon;
  return (
    <span
      data-testid="trigger-kind-badge"
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider',
        SURFACE,
        MUTED,
        'bg-gray-100 dark:bg-gray-800 uiv2:rounded-none uiv2:bg-v2-surface-muted uiv3:rounded-none uiv3:bg-v3-surface-muted'
      )}
    >
      <Icon size={11} aria-hidden />
      {kind.label}
    </span>
  );
};

const ErrorNote: React.FC<{ trigger: RuntimeTrigger; className?: string }> = ({ trigger, className }) => {
  const detail = triggerFailureDetail(trigger);
  if (!detail) return null;
  return (
    <p
      data-testid="trigger-last-error"
      className={cn(
        'text-xs break-words text-red-600 dark:text-red-300 uiv2:text-[#ff6b5e] uiv3:text-v3-accent-bright',
        className
      )}
    >
      {detail}
    </p>
  );
};

/**
 * What the run actually did, rendered verbatim.
 *
 * The backend already stores this as short, readable lines (which tools were
 * authorized, which were invoked, then the automation's own summary), so it is
 * shown with its line breaks preserved rather than reparsed into fields — a
 * status badge alone cannot answer "what happened last time this ran".
 */
const ResultNote: React.FC<{ trigger: RuntimeTrigger; className?: string }> = ({ trigger, className }) => {
  const detail = triggerResultDetail(trigger);
  if (!detail) return null;
  return (
    <p
      data-testid="trigger-result"
      className={cn('whitespace-pre-wrap break-words text-xs', STRONG, className)}
    >
      {detail}
    </p>
  );
};

/** Dense ledger line: one trigger per row, tuned for scanning many at once. */
const RowStyle: React.FC<RendererProps> = ({ trigger, onSelectDefinition }) => {
  const status = STATUS_PRESENTATION[trigger.status];
  const created = formatShortTimestamp(trigger.created_at);
  const retry = formatShortTimestamp(trigger.next_attempt_at);
  const failure = triggerFailureDetail(trigger);
  const resultHeadline = triggerResultHeadline(trigger);
  return (
    <div
      className={cn(
        'flex items-center gap-3 border-b px-3 py-2.5 text-sm last:border-b-0',
        SURFACE,
        status.washClass
      )}
    >
      <span
        aria-hidden
        className={cn('h-2 w-2 shrink-0 rounded-full uiv2:rounded-none uiv3:rounded-none', status.accentClass, status.inFlight && 'animate-pulse')}
      />
      <RunTitle trigger={trigger} onSelectDefinition={onSelectDefinition} className={cn('min-w-0 flex-1 font-medium', STRONG)} />
      <span className={cn('hidden min-w-0 max-w-[12rem] shrink truncate text-xs lg:inline', MONO, MUTED)}>{trigger.task_id}</span>
      <span className={cn('hidden shrink-0 text-xs sm:inline', MUTED)}>{KIND_PRESENTATION[trigger.kind].label}</span>
      <span className={cn('hidden shrink-0 text-xs md:inline', MONO, MUTED)}>{trigger.source}</span>
      {created && <span className={cn('hidden shrink-0 text-xs lg:inline', MUTED)}>{created}</span>}
      <span className={cn('shrink-0 text-xs', MUTED)}>{formatAttempts(trigger.attempt_count)}</span>
      <span className={cn('shrink-0 text-xs font-bold uppercase tracking-wider', status.textClass)}>
        {status.label}
      </span>
      {(failure || resultHeadline || retry) && (
        <span className="hidden min-w-0 max-w-[16rem] shrink xl:block">
          {failure ? (
            <ErrorNote trigger={trigger} className="truncate" />
          ) : resultHeadline ? (
            <span data-testid="trigger-result-headline" className={cn('block truncate text-xs', MUTED)}>
              {resultHeadline}
            </span>
          ) : (
            <span className={cn('block truncate text-xs', MUTED)}>retry {retry}</span>
          )}
        </span>
      )}
    </div>
  );
};

const MetaCell: React.FC<{ label: string; value: string | null }> = ({ label, value }) => (
  <div className="min-w-0">
    <dt className={cn('text-[10px] font-bold uppercase tracking-wider', MUTED)}>{label}</dt>
    <dd className={cn('truncate text-xs', value ? STRONG : MUTED)}>{value ?? '—'}</dd>
  </div>
);

/** Dashboard tile: a status rail, a headline and a lifecycle metadata grid. */
const CardStyle: React.FC<RendererProps> = ({ trigger, onSelectDefinition }) => {
  const status = STATUS_PRESENTATION[trigger.status];
  const kind = KIND_PRESENTATION[trigger.kind];
  const duration = formatDuration(trigger.claimed_at, trigger.completed_at);
  return (
    <div
      className={cn(
        'relative overflow-hidden rounded-xl border pl-4 uiv2:rounded-none uiv3:rounded-none uiv3:border-2',
        SURFACE,
        status.washClass
      )}
    >
      <span aria-hidden className={cn('absolute inset-y-0 left-0 w-1.5', status.accentClass)} />
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <h4 className={cn('flex min-w-0 text-sm font-bold', STRONG)}>
              <RunTitle trigger={trigger} onSelectDefinition={onSelectDefinition} />
            </h4>
            <p className={cn('mt-0.5 text-xs', MUTED)}>{kind.description}</p>
            <p className={cn('mt-0.5 truncate text-[11px]', MONO, MUTED)} data-testid="trigger-task-id">{trigger.task_id}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <KindBadge trigger={trigger} />
            <StatusBadge trigger={trigger} />
          </div>
        </div>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
          <MetaCell label="Created" value={formatTimestamp(trigger.created_at)} />
          <MetaCell label="Claimed" value={formatTimestamp(trigger.claimed_at)} />
          <MetaCell label="Completed" value={formatTimestamp(trigger.completed_at)} />
          <MetaCell label="Attempts" value={formatAttempts(trigger.attempt_count)} />
        </dl>

        <div className={cn('flex flex-wrap items-center gap-x-3 gap-y-1 text-xs', MUTED)}>
          <span className={MONO}>{trigger.source}</span>
          {duration && <span>ran for {duration}</span>}
          {trigger.next_attempt_at && <span>retry after {formatTimestamp(trigger.next_attempt_at)}</span>}
          {trigger.status === 'waiting' && (
            <span data-testid="trigger-waiting-note">
              waiting for the tasks it delegated; resumes automatically when they finish
            </span>
          )}
        </div>

        {triggerResultDetail(trigger) && (
          <div
            className={cn(
              'rounded-lg border px-3 py-2 uiv2:rounded-none uiv3:rounded-none',
              SURFACE,
              'bg-gray-50 dark:bg-gray-900/40 uiv2:bg-v2-surface-muted uiv3:bg-v3-surface-muted'
            )}
          >
            <p className={cn('text-[10px] font-bold uppercase tracking-wider', MUTED)}>Result</p>
            <ResultNote trigger={trigger} className="mt-0.5" />
          </div>
        )}

        {triggerFailureDetail(trigger) && (
          <div
            className={cn(
              'rounded-lg border px-3 py-2 uiv2:rounded-none uiv3:rounded-none',
              'border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/30 uiv2:border-[#5c211d] uiv2:bg-[#1a0907] uiv3:border-v3-accent-dim uiv3:bg-[#1f0a05]'
            )}
          >
            <p className={cn('text-[10px] font-bold uppercase tracking-wider', status.textClass)}>Last error</p>
            <ErrorNote trigger={trigger} className="mt-0.5" />
          </div>
        )}
      </div>
    </div>
  );
};

/** Log-style trace: the lifecycle rendered as a vertical rail of events. */
const TimelineStyle: React.FC<RendererProps> = ({ trigger, onSelectDefinition }) => {
  const status = STATUS_PRESENTATION[trigger.status];
  const steps = lifecycleSteps(trigger);
  return (
    <div className={cn('border-l-2 py-3 pl-4', SURFACE, MONO)}>
      <div className="flex flex-wrap items-center gap-2">
        <RunTitle trigger={trigger} onSelectDefinition={onSelectDefinition} className={cn('text-sm font-bold', STRONG)} />
        <KindBadge trigger={trigger} />
        <StatusBadge trigger={trigger} compact />
        <span className={cn('text-xs', MUTED)}>{formatAttempts(trigger.attempt_count)}</span>
      </div>
      <ol className="mt-2 space-y-1.5">
        {steps.map((step) => (
          <li key={step.key} className="flex items-baseline gap-2 text-xs">
            <span
              aria-hidden
              className={cn(
                'relative top-[3px] h-1.5 w-1.5 shrink-0 rounded-full uiv2:rounded-none uiv3:rounded-none',
                step.reached ? status.accentClass : 'bg-gray-300 dark:bg-gray-600 uiv2:bg-v2-border-bright uiv3:bg-v3-border-bright'
              )}
            />
            <span className={cn('w-20 shrink-0 uppercase tracking-wider', step.reached ? status.textClass : MUTED)}>
              {step.label}
            </span>
            <span className={cn('min-w-0 flex-1 truncate', step.reached ? STRONG : MUTED)}>
              {step.timestamp ?? (step.key === 'retry' ? 'not scheduled' : 'pending')}
            </span>
          </li>
        ))}
      </ol>
      <p className={cn('mt-2 truncate text-xs', MUTED)}>{trigger.task_id}</p>
      <p className={cn('text-xs', MUTED)}>
        source {trigger.source} · id {trigger.trigger_id}
      </p>
      <ResultNote trigger={trigger} className="mt-1" />
      <ErrorNote trigger={trigger} className="mt-1" />
    </div>
  );
};

const STYLE_RENDERERS: Record<TriggerWidgetStyle, React.FC<RendererProps>> = {
  row: RowStyle,
  card: CardStyle,
  timeline: TimelineStyle,
};

/**
 * One runtime trigger rendered as a self-contained widget.
 *
 * `widgetStyle` selects between the styles explored in Storybook so the visual
 * decision can be reviewed side by side rather than guessed at; every style
 * shows the same facts and re-skins itself for all three app shells.
 */
export const TriggerWidget: React.FC<TriggerWidgetProps> = ({
  trigger,
  widgetStyle = 'card',
  onSelectDefinition,
  actions,
  className,
}) => {
  const Renderer = STYLE_RENDERERS[widgetStyle];
  const status = STATUS_PRESENTATION[trigger.status];
  return (
    <article
      data-testid="trigger-widget"
      data-trigger-id={trigger.trigger_id}
      data-status={trigger.status}
      data-kind={trigger.kind}
      data-style={widgetStyle}
      aria-label={`${KIND_PRESENTATION[trigger.kind].label} trigger ${triggerRunTitle(trigger)} (${trigger.task_id}) — ${status.label}`}
      className={className}
    >
      <Renderer trigger={trigger} onSelectDefinition={onSelectDefinition} />
      {actions && <div className="mt-1 flex justify-end px-1 text-xs">{actions}</div>}
    </article>
  );
};
