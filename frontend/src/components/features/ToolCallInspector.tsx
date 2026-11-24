import React, { useId, useState } from 'react';
import { Ban, Check, CheckCircle2, ChevronDown, ChevronRight, Copy, Loader2, Undo2, XCircle } from 'lucide-react';

import { describeUIActionCall, useUIActionLog } from '../../contexts/uiActions';

import { formatToolArgs, isAwaitingApproval } from '../../lib/toolCallEvents';
import type { ToolApprovalAnswer } from '../../contexts/chatSession';
import type { ToolApprovalDecision, ToolCallPhase, ToolCallRecord } from '../../types';
import { ToolApprovalCard } from './ToolApprovalCard';

interface PhaseStyle {
  label: string;
  icon: React.ReactNode;
  chipClass: string;
}

const PHASE_STYLES: Record<ToolCallPhase, PhaseStyle> = {
  start: {
    label: 'running',
    icon: <Loader2 size={11} className="animate-spin" aria-hidden="true" />,
    chipClass: 'border-gray-300/80 text-gray-600 dark:border-gray-600 dark:text-gray-300',
  },
  end: {
    label: 'done',
    icon: <CheckCircle2 size={11} className="text-emerald-600 dark:text-emerald-400" aria-hidden="true" />,
    chipClass: 'border-gray-300/80 text-gray-700 dark:border-gray-600 dark:text-gray-200',
  },
  error: {
    label: 'failed',
    icon: <XCircle size={11} className="text-red-600 dark:text-red-400" aria-hidden="true" />,
    chipClass: 'border-red-300/80 text-red-700 dark:border-red-800 dark:text-red-300',
  },
  blocked: {
    label: 'blocked',
    icon: <Ban size={11} className="text-amber-600 dark:text-amber-400" aria-hidden="true" />,
    chipClass: 'border-amber-300/80 text-amber-800 dark:border-amber-800 dark:text-amber-300',
  },
};

/** How a chip names the user's chat approval decision (JAN-254). */
const APPROVAL_LABELS: Record<ToolApprovalDecision, string> = {
  approved: 'approved by you',
  denied: 'denied by you',
  timeout: 'not answered in time',
  disconnected: 'not answered (disconnected)',
  unavailable: 'needs approval in the web UI',
};

/** "850 ms", "1.2 s", "2 min 5 s"; empty while the call is still running. */
const formatDuration = (durationMs: number | null): string => {
  if (durationMs == null) return '';
  if (durationMs < 1000) return `${Math.round(durationMs)} ms`;
  const seconds = durationMs / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${Math.round(seconds - minutes * 60)} s`;
};

const TruncatedMarker = () => (
  <span className="rounded border border-gray-300 px-1 text-[10px] font-normal uppercase tracking-wide text-gray-500 dark:border-gray-600 dark:text-gray-400">
    truncated
  </span>
);

const PlainBlock: React.FC<{ label: string; text: string; truncated: boolean }> = ({ label, text, truncated }) => (
  <div className="space-y-1">
    <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
      {label}
      {truncated && <TruncatedMarker />}
    </div>
    {/* Untrusted tool text: a plain text node in <pre>, never Markdown or HTML. */}
    <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border border-gray-200 bg-white/80 p-2 font-mono text-[11px] leading-snug text-gray-800 dark:border-gray-700 dark:bg-gray-950/70 dark:text-gray-200">
      {text}
    </pre>
  </div>
);

const ToolCallDetails: React.FC<{ call: ToolCallRecord; id: string }> = ({ call, id }) => {
  const [copied, setCopied] = useState(false);
  const args = formatToolArgs(call.args);
  const approval = call.approval ? APPROVAL_LABELS[call.approval] : null;
  const result = call.phase === 'start' ? '(still running)' : (call.result_preview ?? '(no output)');

  const copy = async () => {
    const text = `Tool: ${call.tool_name}${call.tool_id ? ` (${call.tool_id})` : ''}\nStatus: ${PHASE_STYLES[call.phase].label}${approval ? ` (${approval})` : ''}\n\nArguments:\n${args}\n\nResult:\n${result}`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard unavailable (insecure context, permissions): nothing to do.
    }
  };

  return (
    <div
      id={id}
      role="region"
      aria-label={`Tool call ${call.tool_name}`}
      className="mt-1.5 space-y-2 rounded-md border border-gray-200/80 bg-gray-50/80 p-2 text-[11px] dark:border-gray-700 dark:bg-gray-900/60"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 truncate text-gray-600 dark:text-gray-300">
          <span className="font-mono font-medium">{call.tool_name}</span>
          {call.tool_id && <span className="text-gray-400"> · {call.tool_id}</span>}
          <span className="text-gray-400"> · {PHASE_STYLES[call.phase].label}</span>
          {approval && <span className="text-gray-400"> · {approval}</span>}
          {call.duration_ms != null && <span className="text-gray-400"> · {formatDuration(call.duration_ms)}</span>}
        </div>
        <button
          type="button"
          onClick={() => void copy()}
          className="inline-flex shrink-0 items-center gap-1 rounded border border-gray-300 px-1.5 py-0.5 text-[10px] text-gray-600 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
          aria-label={`Copy tool call ${call.tool_name}`}
        >
          {copied ? <Check size={11} aria-hidden="true" /> : <Copy size={11} aria-hidden="true" />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <PlainBlock label="Arguments" text={args} truncated={call.args_truncated} />
      <PlainBlock label="Result" text={result} truncated={call.result_truncated} />
    </div>
  );
};

/** The Undo button of a live page action (JAN-251); "undone" once used, nothing when undo is not cheap. */
const UIActionUndo: React.FC<{ name: string; undone: boolean; onUndo?: () => void }> = ({ name, undone, onUndo }) => {
  if (undone) return <span className="text-[10px] text-gray-400">undone</span>;
  if (!onUndo) return null;
  return (
    <button
      type="button"
      onClick={onUndo}
      aria-label={`Undo ${name}`}
      className="inline-flex items-center gap-0.5 rounded-full border border-gray-300 px-1.5 py-0.5 text-[10px] text-gray-600 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
    >
      <Undo2 size={10} aria-hidden="true" />
      Undo
    </button>
  );
};

interface ToolCallInspectorProps {
  calls: ToolCallRecord[];
  /** Answers a paused confirmation-required call (JAN-254); without it, cards cannot be answered. */
  onAnswerApproval?: (callId: string, decision: ToolApprovalAnswer) => void;
}

/**
 * The tool calls of one assistant chat message (JAN-249): a row of chips (status
 * icon, name, duration); clicking a chip expands its redacted arguments and result
 * preview inline. Expandable rather than hover, so it works on touch and for long
 * output. Tool output is untrusted and only ever shown as plain monospace text.
 *
 * A call waiting for the user's Approve / Deny (JAN-254) is shown as an inline
 * `ToolApprovalCard` instead of a chip until its next tool event arrives.
 *
 * A page action (JAN-251) is named by its verb and target (`open_item · Invoice
 * overdue`) instead of the tool name and, while it is live in this tab, gets an
 * Undo button where undo is cheap (a client-side affordance, never a tool call).
 */
export const ToolCallInspector: React.FC<ToolCallInspectorProps> = ({ calls, onAnswerApproval }) => {
  const [openCallId, setOpenCallId] = useState<string | null>(null);
  const uiActions = useUIActionLog();
  const baseId = useId();
  if (calls.length === 0) return null;
  const waiting = calls.filter(isAwaitingApproval);
  const chips = calls.filter((call) => !isAwaitingApproval(call));
  const openCall = chips.find((call) => call.call_id === openCallId) ?? null;
  const detailsId = `${baseId}-details`;

  return (
    <div className="mt-2 not-prose" data-testid="tool-call-inspector">
      {waiting.map((call) =>
        call.approvalRequest ? (
          <ToolApprovalCard
            key={call.call_id}
            request={call.approvalRequest}
            answer={call.approvalAnswer}
            onAnswer={onAnswerApproval}
          />
        ) : null
      )}
      {chips.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label="Tool calls">
          {chips.map((call) => {
            const style = PHASE_STYLES[call.phase];
            const isOpen = call.call_id === openCallId;
            const duration = formatDuration(call.duration_ms);
            const approval = call.approval ? APPROVAL_LABELS[call.approval] : '';
            const executed = uiActions?.executed[call.call_id];
            const name = describeUIActionCall(call, executed) ?? call.tool_name;
            return (
              <li key={call.call_id} className="inline-flex items-center gap-1">
                <button
                  type="button"
                  aria-expanded={isOpen}
                  aria-controls={isOpen ? detailsId : undefined}
                  aria-label={`${name}, ${style.label}${approval ? `, ${approval}` : ''}${duration ? `, ${duration}` : ''}`}
                  onClick={() => setOpenCallId(isOpen ? null : call.call_id)}
                  className={`inline-flex items-center gap-1 rounded-full border bg-white/80 px-2 py-0.5 text-[11px] transition-colors hover:bg-gray-100 dark:bg-gray-900/70 dark:hover:bg-gray-800 ${style.chipClass} ${
                    isOpen ? 'ring-1 ring-primary/50' : ''
                  }`}
                >
                  {style.icon}
                  <span className="max-w-[16rem] truncate font-mono">{name}</span>
                  {duration && <span className="text-gray-400">{duration}</span>}
                  {isOpen ? <ChevronDown size={11} aria-hidden="true" /> : <ChevronRight size={11} aria-hidden="true" />}
                </button>
                {executed && <UIActionUndo name={name} undone={executed.undone} onUndo={executed.undo && (() => uiActions?.undo(call.call_id))} />}
              </li>
            );
          })}
        </ul>
      )}
      {openCall && <ToolCallDetails key={openCall.call_id} call={openCall} id={detailsId} />}
    </div>
  );
};
