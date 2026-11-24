import React, { useId } from 'react';
import { Check, CheckCheck, ShieldAlert, X } from 'lucide-react';

import { formatToolArgs } from '../../lib/toolCallEvents';
import type { ToolApprovalAnswer } from '../../contexts/chatSession';
import type { ToolApprovalRequest } from '../../types';

/** "5 min", "90 s"; how long the card stays open before the call counts as declined. */
const formatTimeout = (seconds: number): string => {
  if (!seconds || seconds <= 0) return '';
  if (seconds < 120) return `${Math.round(seconds)} s`;
  return `${Math.round(seconds / 60)} min`;
};

interface ToolApprovalCardProps {
  request: ToolApprovalRequest;
  /** The answer this tab already sent, if any: the buttons are then disabled. */
  answer?: ToolApprovalAnswer;
  onAnswer?: (callId: string, decision: ToolApprovalAnswer) => void;
}

/**
 * Inline Approve / Deny card for a paused `confirmation="required"` chat tool call
 * (JAN-254). Approve covers only this call, with exactly these arguments; Always allow
 * (JAN-359) also lets this tool run in chat without asking from now on.
 * Arguments are redacted by the backend and shown as plain monospace text only.
 */
export const ToolApprovalCard: React.FC<ToolApprovalCardProps> = ({ request, answer, onAnswer }) => {
  const titleId = useId();
  const timeout = formatTimeout(request.timeout_seconds);
  const answered = answer != null;

  return (
    <section
      role="group"
      aria-labelledby={titleId}
      data-testid="tool-approval-card"
      className="mt-2 space-y-2 rounded-md border border-amber-300/80 bg-amber-50/80 p-2.5 text-[12px] text-gray-800 not-prose dark:border-amber-700/70 dark:bg-amber-950/30 dark:text-gray-100"
    >
      <div className="flex items-start gap-2">
        <ShieldAlert size={14} className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden="true" />
        <div className="min-w-0 space-y-0.5">
          <h4 id={titleId} className="m-0 text-[12px] font-semibold">
            Janus wants to use {request.display_name}
          </h4>
          <p className="m-0 text-[11px] text-gray-600 dark:text-gray-300">
            <span className="font-mono">{request.tool_name}</span>
            {request.tool_id && <span className="text-gray-400"> · {request.tool_id}</span>}
            {request.risk_level && <span className="text-gray-400"> · {request.risk_level} risk</span>}
          </p>
          {request.reason && <p className="m-0 text-[11px] text-gray-600 dark:text-gray-300">{request.reason}</p>}
        </div>
      </div>
      <div className="space-y-1">
        <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
          Arguments
          {request.args_truncated && (
            <span className="rounded border border-gray-300 px-1 text-[10px] font-normal uppercase tracking-wide text-gray-500 dark:border-gray-600 dark:text-gray-400">
              truncated
            </span>
          )}
        </div>
        {/* Model-written arguments: a plain text node in <pre>, never Markdown or HTML. */}
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border border-gray-200 bg-white/80 p-2 font-mono text-[11px] leading-snug text-gray-800 dark:border-gray-700 dark:bg-gray-950/70 dark:text-gray-200">
          {formatToolArgs(request.args)}
        </pre>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={answered || !onAnswer}
          onClick={() => onAnswer?.(request.call_id, 'approve')}
          className="inline-flex items-center gap-1 rounded border border-emerald-600 bg-emerald-600 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <Check size={12} aria-hidden="true" />
          {answer === 'approve' ? 'Approving…' : 'Approve'}
        </button>
        <button
          type="button"
          disabled={answered || !onAnswer}
          onClick={() => onAnswer?.(request.call_id, 'always')}
          title="Approve this call and stop asking for this tool in chat (change it in Settings → Tools & Connections)"
          className="inline-flex items-center gap-1 rounded border border-emerald-600 bg-white px-2.5 py-1 text-[11px] font-medium text-emerald-700 hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-gray-900 dark:text-emerald-300 dark:hover:bg-emerald-950/40"
        >
          <CheckCheck size={12} aria-hidden="true" />
          {answer === 'always' ? 'Approving…' : 'Always allow'}
        </button>
        <button
          type="button"
          disabled={answered || !onAnswer}
          onClick={() => onAnswer?.(request.call_id, 'deny')}
          className="inline-flex items-center gap-1 rounded border border-gray-300 bg-white px-2.5 py-1 text-[11px] font-medium text-gray-700 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-200 dark:hover:bg-gray-800"
        >
          <X size={12} aria-hidden="true" />
          {answer === 'deny' ? 'Denying…' : 'Deny'}
        </button>
        <span className="text-[10px] text-gray-500 dark:text-gray-400">
          Approve runs once, only for this call.{timeout && ` Counts as denied after ${timeout} without an answer.`}
        </span>
      </div>
    </section>
  );
};
