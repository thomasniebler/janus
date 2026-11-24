import React from 'react';
import { AlertTriangle, CheckCircle2, CircleDashed, Loader2, ShieldAlert, XCircle, type LucideIcon } from 'lucide-react';

import type { RuntimeStepTrace, RuntimeTaskStep } from '../../../lib/runtimeTasks';

const STEP_ICONS: Record<string, { icon: LucideIcon; className: string; label: string }> = {
  pending: { icon: CircleDashed, className: 'text-gray-400', label: 'Pending' },
  running: { icon: Loader2, className: 'animate-spin text-primary', label: 'Running' },
  completed: { icon: CheckCircle2, className: 'text-green-500 uiv3:text-v3-text', label: 'Completed' },
  failed: { icon: XCircle, className: 'text-red-500', label: 'Failed' },
};

const chipClassName =
  'rounded-md border border-gray-200 bg-gray-50 px-1.5 py-0.5 font-mono text-[11px] dark:border-gray-700 dark:bg-gray-900/50 uiv2:rounded-none uiv3:rounded-none';

const ToolChips: React.FC<{ label: string; tools: string[] }> = ({ label, tools }) => (
  <div className="flex flex-wrap items-center gap-1.5 text-xs">
    <span className="text-gray-500 dark:text-gray-400">{label}:</span>
    {tools.length === 0 ? (
      <span className="text-gray-400">none</span>
    ) : (
      tools.map((tool, index) => (
        <code key={`${tool}-${index}`} className={chipClassName}>{tool}</code>
      ))
    )}
  </div>
);

/** One trace line that deserves attention: a refusal, a failure, an unavailable tool, a suspected injection. */
const TraceWarning: React.FC<{ label: string; text: string | null; icon?: LucideIcon }> = ({ label, text, icon: Icon = AlertTriangle }) =>
  text ? (
    <p className="flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-300">
      <Icon size={14} aria-hidden className="mt-0.5 shrink-0" />
      <span><span className="font-semibold">{label}:</span> <span className="break-words">{text}</span></span>
    </p>
  ) : null;

/** The tool trace recorded for one executed step. */
const StepTrace: React.FC<{ trace: RuntimeStepTrace }> = ({ trace }) => (
  <div className="space-y-1.5" data-testid="task-step-trace">
    {trace.model && <p className="text-xs text-gray-500 dark:text-gray-400">Model: <code>{trace.model}</code></p>}
    <ToolChips label="Authorized" tools={trace.authorized_tools} />
    <ToolChips label="Invoked" tools={trace.invoked_tools} />
    {trace.tool_selection && (
      <p className="break-words text-xs text-gray-500 dark:text-gray-400">
        <span className="font-semibold">Tool selection:</span> {trace.tool_selection}
      </p>
    )}
    <TraceWarning label="Refused" text={trace.refused} icon={ShieldAlert} />
    <TraceWarning label="Failed calls" text={trace.failed} />
    <TraceWarning label="Unavailable" text={trace.skipped} />
    <TraceWarning label="Suspected prompt injection" text={trace.suspected_injection} icon={ShieldAlert} />
  </div>
);

const TaskStepItem: React.FC<{ step: RuntimeTaskStep; index: number }> = ({ step, index }) => {
  const presentation = STEP_ICONS[step.status] ?? STEP_ICONS.pending;
  const Icon = presentation.icon;
  return (
    <li className="rounded-xl border border-gray-200 p-3 dark:border-gray-700 uiv2:rounded-none uiv3:rounded-none" data-testid="task-step" data-step-status={step.status}>
      <div className="flex items-start gap-2">
        <Icon size={16} className={`mt-0.5 shrink-0 ${presentation.className}`} aria-label={presentation.label} />
        <div className="min-w-0 flex-1 space-y-2">
          <p className="break-words text-sm font-medium">
            <span className="text-gray-400">Step {index + 1}.</span> {step.objective}
          </p>
          {step.required_tool_ids.length > 0 && <ToolChips label="Requires" tools={step.required_tool_ids} />}
          {step.trace && <StepTrace trace={step.trace} />}
          {step.error && <p role="alert" className="break-words text-xs text-red-500">Error: {step.error}</p>}
          {step.output && (
            <details className="text-sm">
              <summary className="cursor-pointer text-xs font-medium text-primary">Answer</summary>
              {/* Untrusted model text: plain text only, never markdown or HTML. */}
              <p className="mt-1 whitespace-pre-wrap break-words text-gray-700 dark:text-gray-300">{step.output}</p>
            </details>
          )}
        </div>
      </div>
    </li>
  );
};

/** A task's persisted plan steps with each step's tool trace (JAN-204). */
export const TaskStepList: React.FC<{ steps: RuntimeTaskStep[] }> = ({ steps }) =>
  steps.length === 0 ? (
    <p className="text-sm text-gray-500 dark:text-gray-400">No plan recorded yet. Steps appear once the task starts running.</p>
  ) : (
    <ol aria-label="Plan steps" className="space-y-2">
      {steps.map((step, index) => (
        <TaskStepItem key={step.step_id} step={step} index={index} />
      ))}
    </ol>
  );
