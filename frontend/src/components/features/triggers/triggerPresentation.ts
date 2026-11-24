import { formatServerDateTime, parseServerDate } from '../../../lib/serverTime';
import {
  AlertOctagon,
  Ban,
  CalendarClock,
  CheckCircle2,
  CircleDashed,
  Hand,
  Hourglass,
  Loader2,
  Radio,
  XCircle,
  type LucideIcon,
} from 'lucide-react';

import type { RuntimeTrigger, RuntimeTriggerKind, RuntimeTriggerStatus } from '../../../types';

/**
 * Shared vocabulary for every trigger widget style.
 *
 * The three app shells (UIv1 glass, UIv2 phosphor terminal, UIv3 rusty console)
 * re-skin the same markup through the `uiv2:`/`uiv3:` Tailwind variants, so each
 * status and kind carries its palette here once instead of being forked per shell.
 */

export interface StatusPresentation {
  /** Human label used in badges and accessible descriptions. */
  label: string;
  icon: LucideIcon;
  /** Foreground colour for the status word and its icon. */
  textClass: string;
  /** Filled pill treatment (background + text + border). */
  badgeClass: string;
  /** Solid fill used for accent rails, dots and timeline nodes. */
  accentClass: string;
  /** Soft wash used behind a whole widget when the state deserves emphasis. */
  washClass: string;
  /** Whether the status is still in motion and should animate. */
  inFlight: boolean;
  /** Whether the status is a failure, so error detail must be shown. */
  isFailure: boolean;
}

export interface KindPresentation {
  label: string;
  icon: LucideIcon;
  /** One short line explaining where this occurrence came from. */
  description: string;
}

export const STATUS_PRESENTATION: Record<RuntimeTriggerStatus, StatusPresentation> = {
  queued: {
    label: 'Queued',
    icon: CircleDashed,
    // UIv3's palette is almost entirely rust, so "waiting" is carried by a muted
    // tone rather than another shade of red that would read as a failure.
    textClass: 'text-amber-600 dark:text-amber-300 uiv2:text-[#d9c25a] uiv3:text-v3-text-muted',
    badgeClass:
      'border-amber-300 bg-amber-100 text-amber-700 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-300 uiv2:rounded-none uiv2:border-[#5c5120] uiv2:bg-[#2a2510] uiv2:text-[#d9c25a] uiv3:rounded-none uiv3:border-v3-border-bright uiv3:bg-v3-surface-muted uiv3:text-v3-text-muted',
    accentClass: 'bg-amber-400 uiv2:bg-[#d9c25a] uiv3:bg-v3-border-bright',
    washClass: 'uiv2:bg-[#d9c25a]/[0.04] uiv3:bg-v3-surface-muted/40',
    inFlight: false,
    isFailure: false,
  },
  running: {
    label: 'Running',
    icon: Loader2,
    // Ember (not rust red) in UIv3, so "in flight" is never confused with "failed".
    // Explicit blues rather than `text-primary`/`bg-primary`: UIv2 rewrites those
    // two utilities globally to its lime accent, which would collide with
    // "completed" and make a running trigger look finished.
    textClass: 'text-blue-600 dark:text-blue-300 uiv2:text-v2-cyan uiv3:text-v3-ember',
    badgeClass:
      'border-blue-300 bg-blue-100 text-blue-700 dark:border-blue-800 dark:bg-blue-900/30 dark:text-blue-300 uiv2:rounded-none uiv2:border-v2-cyan/40 uiv2:bg-v2-cyan/10 uiv2:text-v2-cyan uiv3:rounded-none uiv3:border-v3-ember/60 uiv3:bg-v3-ember/10 uiv3:text-v3-ember',
    accentClass: 'bg-blue-500 uiv2:bg-v2-cyan uiv3:bg-v3-ember',
    washClass: 'uiv2:bg-v2-cyan/[0.05] uiv3:bg-v3-ember/[0.05]',
    inFlight: true,
    isFailure: false,
  },
  waiting: {
    label: 'Waiting',
    icon: Hourglass,
    // Parked until the tasks it delegated finish (JAN-207): paused, not failed and
    // not spinning, so a violet tone distinct from queued amber and running blue.
    textClass: 'text-violet-600 dark:text-violet-300 uiv2:text-[#b48ead] uiv3:text-[#c9a46a]',
    badgeClass:
      'border-violet-300 bg-violet-100 text-violet-700 dark:border-violet-800 dark:bg-violet-900/30 dark:text-violet-300 uiv2:rounded-none uiv2:border-[#5a4157] uiv2:bg-[#241a23] uiv2:text-[#b48ead] uiv3:rounded-none uiv3:border-[#5c4a2c] uiv3:bg-[#1f1a10] uiv3:text-[#c9a46a]',
    accentClass: 'bg-violet-500 uiv2:bg-[#b48ead] uiv3:bg-[#c9a46a]',
    washClass: 'uiv2:bg-[#b48ead]/[0.04] uiv3:bg-[#c9a46a]/[0.04]',
    inFlight: false,
    isFailure: false,
  },
  completed: {
    label: 'Completed',
    icon: CheckCircle2,
    textClass: 'text-green-600 dark:text-green-400 uiv2:text-v2-accent uiv3:text-[#9bbf6a]',
    badgeClass:
      'border-green-300 bg-green-100 text-green-700 dark:border-green-800 dark:bg-green-900/30 dark:text-green-300 uiv2:rounded-none uiv2:border-v2-accent/40 uiv2:bg-v2-accent-soft uiv2:text-v2-accent uiv3:rounded-none uiv3:border-[#4d5c33] uiv3:bg-[#1b2113] uiv3:text-[#9bbf6a]',
    accentClass: 'bg-green-500 uiv2:bg-v2-accent uiv3:bg-[#9bbf6a]',
    washClass: 'uiv2:bg-v2-accent-soft/50 uiv3:bg-[#9bbf6a]/[0.04]',
    inFlight: false,
    isFailure: false,
  },
  failed: {
    label: 'Failed',
    icon: XCircle,
    textClass: 'text-red-600 dark:text-red-400 uiv2:text-[#ff6b5e] uiv3:text-v3-accent-bright',
    badgeClass:
      'border-red-300 bg-red-100 text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-300 uiv2:rounded-none uiv2:border-[#5c211d] uiv2:bg-[#2a0f0d] uiv2:text-[#ff6b5e] uiv3:rounded-none uiv3:border-v3-accent uiv3:bg-v3-accent-soft uiv3:text-v3-accent-bright',
    accentClass: 'bg-red-500 uiv2:bg-[#ff6b5e] uiv3:bg-v3-accent',
    washClass: 'bg-red-500/[0.03] uiv2:bg-[#ff6b5e]/[0.05] uiv3:bg-v3-accent-soft',
    inFlight: false,
    isFailure: true,
  },
  exhausted: {
    label: 'Exhausted',
    icon: AlertOctagon,
    textClass: 'text-red-700 dark:text-red-300 uiv2:text-[#ff8a7d] uiv3:text-v3-accent',
    badgeClass:
      'border-red-400 bg-red-200 text-red-800 dark:border-red-900 dark:bg-red-950/50 dark:text-red-200 uiv2:rounded-none uiv2:border-[#7a2a24] uiv2:bg-[#3a1512] uiv2:text-[#ff8a7d] uiv3:rounded-none uiv3:border-v3-accent-dim uiv3:bg-[#2a0f08] uiv3:text-v3-accent',
    accentClass: 'bg-red-700 uiv2:bg-[#ff8a7d] uiv3:bg-v3-accent-dim',
    washClass: 'bg-red-500/[0.05] uiv2:bg-[#ff8a7d]/[0.06] uiv3:bg-v3-accent-soft',
    inFlight: false,
    isFailure: true,
  },
  cancelled: {
    label: 'Cancelled',
    icon: Ban,
    // A deliberate, user-initiated stop -- neutral, not a failure tone.
    textClass: 'text-gray-500 dark:text-gray-400 uiv2:text-v2-text-muted uiv3:text-v3-text-muted',
    badgeClass:
      'border-gray-300 bg-gray-100 text-gray-600 dark:border-gray-700 dark:bg-gray-800/60 dark:text-gray-300 uiv2:rounded-none uiv2:border-v2-text-muted/30 uiv2:bg-v2-surface-muted uiv2:text-v2-text-muted uiv3:rounded-none uiv3:border-v3-border-bright uiv3:bg-v3-surface-muted uiv3:text-v3-text-muted',
    accentClass: 'bg-gray-400 uiv2:bg-v2-text-muted uiv3:bg-v3-text-muted',
    washClass: 'uiv2:bg-v2-text-muted/[0.04] uiv3:bg-v3-surface-muted/40',
    inFlight: false,
    isFailure: false,
  },
};

export const KIND_PRESENTATION: Record<RuntimeTriggerKind, KindPresentation> = {
  manual: {
    label: 'Manual',
    icon: Hand,
    description: 'Started on demand: Run now, or queued by hand.',
  },
  scheduled: {
    label: 'Scheduled',
    icon: CalendarClock,
    description: 'One due occurrence of a schedule or a Janus follow-up.',
  },
  event: {
    label: 'Event',
    icon: Radio,
    description: 'Started by an event such as a new email.',
  },
};

export const RUNTIME_TRIGGER_STATUSES: RuntimeTriggerStatus[] = [
  'queued',
  'running',
  'waiting',
  'completed',
  'failed',
  'exhausted',
  'cancelled',
];

export const RUNTIME_TRIGGER_KINDS: RuntimeTriggerKind[] = ['manual', 'scheduled', 'event'];

/** Format an ISO timestamp for display, tolerating null and unparseable values. */
export const formatTimestamp = (value?: string | null): string | null => formatServerDateTime(value);

/** Short clock-only rendering used by the dense row style. */
export const formatShortTimestamp = (value?: string | null): string | null =>
  formatServerDateTime(value, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });

/**
 * Elapsed time between two lifecycle timestamps, as a compact `1m 04s` string.
 *
 * Returns null when either end is missing or the pair is out of order, so a
 * half-written lifecycle never renders a negative or nonsensical duration.
 */
export const formatDuration = (from?: string | null, to?: string | null): string | null => {
  const start = parseServerDate(from)?.getTime();
  const end = parseServerDate(to)?.getTime();
  if (start === undefined || end === undefined || end < start) return null;
  const totalSeconds = Math.round((end - start) / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return `${minutes}m ${String(seconds).padStart(2, '0')}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`;
};

export const formatAttempts = (count: number): string =>
  `${count} attempt${count === 1 ? '' : 's'}`;

export interface LifecycleStep {
  key: 'created' | 'claimed' | 'completed' | 'retry';
  label: string;
  timestamp: string | null;
  /** True once this step has actually happened. */
  reached: boolean;
}

/**
 * The ordered lifecycle a trigger walks, including steps it has not reached yet.
 *
 * Unreached steps are kept (rather than filtered out) so the timeline style can
 * show what is still outstanding instead of only what already happened.
 */
export const lifecycleSteps = (trigger: RuntimeTrigger): LifecycleStep[] => {
  const status = STATUS_PRESENTATION[trigger.status];
  const terminalLabel = status.isFailure ? 'Stopped' : 'Completed';
  const steps: LifecycleStep[] = [
    {
      key: 'created',
      label: 'Created',
      timestamp: formatTimestamp(trigger.created_at),
      reached: Boolean(trigger.created_at),
    },
    {
      key: 'claimed',
      label: 'Claimed',
      timestamp: formatTimestamp(trigger.claimed_at),
      reached: Boolean(trigger.claimed_at),
    },
    {
      key: 'completed',
      label: terminalLabel,
      timestamp: formatTimestamp(trigger.completed_at),
      reached: Boolean(trigger.completed_at),
    },
  ];
  if (trigger.next_attempt_at) {
    steps.push({
      key: 'retry',
      label: 'Retry due',
      timestamp: formatTimestamp(trigger.next_attempt_at),
      reached: false,
    });
  }
  return steps;
};

/**
 * The failure a human should actually be shown for a trigger.
 *
 * `last_error` is the live transport-level error, but the worker clears it
 * every time it re-claims a row for a retry — so an exhausted trigger can end
 * up with no `last_error` at all. `step_error`, decoded from the durable plan
 * state, survives that and is the fallback (JAN-192).
 */
export const triggerFailureDetail = (trigger: RuntimeTrigger): string | null =>
  trigger.last_error?.trim() || trigger.step_error?.trim() || null;

/** The executed run's recorded result, or null when nothing has run yet. */
export const triggerResultDetail = (trigger: RuntimeTrigger): string | null =>
  trigger.result?.trim() || null;

/** A single-line condensation of the result, for the dense row style. */
export const triggerResultHeadline = (trigger: RuntimeTrigger): string | null => {
  const result = triggerResultDetail(trigger);
  if (!result) return null;
  // The stored result leads with `Authorized tools:` / `Tools invoked:` lines
  // and then the automation's own summary after a blank line. The summary is
  // the interesting part when there is only room for one line.
  const [, summary] = result.split(/\n\s*\n/);
  return (summary ?? result).split('\n')[0].trim() || null;
};

/** Source recorded on follow-ups Janus schedules from chat (mirrors `AGENT_TRIGGER_SOURCE`). */
const AGENT_TRIGGER_SOURCE = 'agent-tool';

/**
 * The human name of a run: its definition's name, or what kind of ad-hoc run
 * it is. The raw task id is an identifier, not a title (JAN-256).
 */
export const triggerRunTitle = (trigger: RuntimeTrigger): string => {
  if (trigger.definition_name) return trigger.definition_name;
  return trigger.source === AGENT_TRIGGER_SOURCE ? 'Janus follow-up' : 'Ad-hoc run';
};
