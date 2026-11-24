import { formatServerDateTime, parseServerDate } from '../../../lib/serverTime';
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Check, Pencil, Sparkles, X } from 'lucide-react';
import { mcpApi, triggerDefinitionApi } from '../../../api';
import type {
  MCPBuiltin,
  TriggerDefinitionInput,
  TriggerProposal,
  TriggerProposalApproval,
  TriggerProposalSupersedes,
  TriggerSchedule,
  TriggerSubsystemSpec,
} from '../../../types';
import { Badge } from '../../ui/Badge';
import { Button } from '../../ui/Button';
import { Card, CardContent, CardHeader } from '../../ui/Card';
import { Modal } from '../../ui/Modal';
import { DefinitionEditor } from './TriggerDefinitionManager';
import { TriggerEventOptionsSummary } from './TriggerEventFilterEditor';
import { triggerDefinitionQueryKey } from './triggerDefinitionQueries';
import { describeCatalogEvent, triggerEventCatalogQueryKey } from './triggerEventCatalog';
import { triggerProposalQueryKey } from './triggerProposalCount';

/**
 * Settings -> Triggers, "Suggested automations" (JAN-235).
 *
 * Janus proposes, the user approves. Every card shows exactly what would run:
 * the objective verbatim, the activation in the proposal's timezone, every
 * tool, and -- for tools that are off for unattended runs -- an explicit
 * per-tool checkbox (unticked ones are dropped on approval). Janus-written
 * text (rationale, objective, names) is rendered as plain text only.
 */

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const HISTORY_SIZE = 5;
const UNTRUSTED_LABEL: Record<string, string> = {
  mail: 'email',
  whatsapp: 'WhatsApp',
  web_search: 'web',
  web_fetch: 'web pages',
  knowledge: 'stored documents',
  github: 'GitHub',
  linear: 'Linear',
};

const describeSchedule = (schedule: TriggerSchedule | null, timezone: string): string => {
  if (!schedule) return 'Schedule (not configured)';
  if (schedule.type === 'interval') return `Every ${schedule.minutes} minutes`;
  if (schedule.type === 'once') return `Once at ${schedule.at ? (formatServerDateTime(schedule.at) ?? schedule.at) : '?'}`;
  const days = schedule.days && schedule.days.length > 0 && schedule.days.length < 7
    ? schedule.days.map((day) => WEEKDAYS[day]).join(', ')
    : 'every day';
  return `Daily at ${schedule.time} (${days}, ${timezone})`;
};

const describeActivation = (
  item: Pick<TriggerProposalSupersedes, 'kind' | 'schedule' | 'timezone' | 'event_type'> & Pick<TriggerProposal, 'event_filter'>,
  catalog: TriggerSubsystemSpec[]
): string => {
  if (item.kind === 'event') return `${item.event_filter?.length ? 'On matching events' : 'On every event'}: ${describeCatalogEvent(catalog, item.event_type)}`;
  if (item.kind === 'manual') return 'Manual (only when you press Run now)';
  return describeSchedule(item.schedule, item.timezone);
};

const formatInZone = (value: string, timezone: string): string => {
  const parsed = parseServerDate(value);
  if (!parsed) return value;
  try {
    return parsed.toLocaleString(undefined, { timeZone: timezone, dateStyle: 'medium', timeStyle: 'short' });
  } catch {
    return parsed.toLocaleString();
  }
};

const errorDetail = (error: unknown, fallback: string): string => {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
};

const toolName = (tools: Map<string, MCPBuiltin>, toolId: string): string => tools.get(toolId)?.display_name ?? toolId;

const DiffRow: React.FC<{ label: string; before: string; after: string }> = ({ label, before, after }) => (
  <div className="grid gap-2 md:grid-cols-[8rem_1fr_1fr]" data-changed={before !== after || undefined}>
    <span className="text-xs font-bold uppercase tracking-wider text-gray-400">{label}</span>
    <span className="whitespace-pre-wrap break-words rounded-lg bg-gray-50 p-2 text-xs dark:bg-gray-900/60">{before}</span>
    <span
      className={`whitespace-pre-wrap break-words rounded-lg p-2 text-xs ${
        before !== after ? 'bg-amber-50 text-amber-900 dark:bg-amber-900/20 dark:text-amber-200' : 'bg-gray-50 dark:bg-gray-900/60'
      }`}
    >
      {after}
    </span>
  </div>
);

const SupersedeDiff: React.FC<{ proposal: TriggerProposal; tools: Map<string, MCPBuiltin>; catalog: TriggerSubsystemSpec[] }> = ({
  proposal,
  tools,
  catalog,
}) => {
  const current = proposal.supersedes;
  if (!current) return null;
  const list = (ids: string[]) => ids.map((id) => toolName(tools, id)).join(', ') || 'none';
  return (
    <section aria-label="Changes against the approved automation" className="space-y-2 rounded-xl border border-gray-200 p-3 dark:border-gray-800">
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Replaces <strong>{current.name}</strong>, which keeps running as approved until you approve this.
      </p>
      <div className="hidden gap-2 text-[10px] font-bold uppercase tracking-wider text-gray-400 md:grid md:grid-cols-[8rem_1fr_1fr]">
        <span />
        <span>Currently approved</span>
        <span>Suggested</span>
      </div>
      <DiffRow label="Objective" before={current.objective} after={proposal.objective} />
      <DiffRow label="Tools" before={list(current.tool_ids)} after={list(proposal.tool_ids)} />
      <DiffRow label="When" before={describeActivation(current, catalog)} after={describeActivation(proposal, catalog)} />
    </section>
  );
};

interface ProposalCardProps {
  proposal: TriggerProposal;
  tools: Map<string, MCPBuiltin>;
  catalog: TriggerSubsystemSpec[];
  busy: boolean;
  error: string | null;
  onApprove: (proposal: TriggerProposal, approval: TriggerProposalApproval) => void;
  onEdit: (proposal: TriggerProposal, toolIds: string[]) => void;
  onReject: (proposal: TriggerProposal, reason: string) => void;
}

const ProposalCard: React.FC<ProposalCardProps> = ({ proposal, tools, catalog, busy, error, onApprove, onEdit, onReject }) => {
  const [allowedBlocked, setAllowedBlocked] = useState<Set<string>>(new Set());
  const [enableNow, setEnableNow] = useState(true);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const blocked = new Set(proposal.blocked_tool_ids);
  // Unticked blocked tools are dropped: approving never grants one by default.
  const approvedToolIds = proposal.tool_ids.filter((id) => !blocked.has(id) || allowedBlocked.has(id));
  const toggleBlocked = (toolId: string, checked: boolean) =>
    setAllowedBlocked((current) => {
      const next = new Set(current);
      if (checked) next.add(toolId);
      else next.delete(toolId);
      return next;
    });
  const untrusted = proposal.untrusted_context.map((id) => UNTRUSTED_LABEL[id] ?? id);
  const headingId = `trigger-proposal-${proposal.id}-name`;

  return (
    <article
      aria-labelledby={headingId}
      data-testid={`trigger-proposal-${proposal.id}`}
      className="space-y-3 rounded-xl border border-amber-300/70 p-4 dark:border-amber-700/60"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h4 id={headingId} className="font-semibold">{proposal.name}</h4>
        <Badge variant="warning">Suggested by Janus</Badge>
        {proposal.supersedes && <Badge variant="secondary">Replacement</Badge>}
        {untrusted.length > 0 && (
          <Badge variant="danger">
            <span data-testid="proposal-provenance">Drafted after reading {untrusted.join(' / ')} content</span>
          </Badge>
        )}
      </div>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        {describeActivation(proposal, catalog)}
        {proposal.next_occurrence && ` · would first run ${formatInZone(proposal.next_occurrence, proposal.timezone)} (${proposal.timezone})`}
      </p>
      <TriggerEventOptionsSummary definition={proposal} />
      {proposal.injection_suspected && (
        <p data-testid="proposal-injection-warning" role="note" className="flex items-start gap-1.5 rounded-lg border border-red-400 bg-red-50 p-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-900/20 dark:text-red-200">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          Something Janus read just before drafting this looked like a prompt injection (instructions hidden in outside
          content). It was saved only as a suggestion. Approve it only if you asked for exactly this.
        </p>
      )}
      {proposal.kind === 'event' && proposal.event_type === 'mail.received' && (
        <p className="flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-400">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          Runs on {proposal.event_filter?.length ? 'new emails that match its filter' : 'every new email'}. The email itself is untrusted input that this automation will read.
        </p>
      )}

      <div>
        <span className="text-[10px] font-bold uppercase tracking-wider text-gray-400">Objective (runs exactly as written)</span>
        <p data-testid="proposal-objective" className="mt-1 whitespace-pre-wrap break-words rounded-lg bg-gray-50 p-3 font-mono text-xs dark:bg-gray-900/60">
          {proposal.objective}
        </p>
      </div>

      {proposal.proposal_rationale && (
        <div>
          <span className="text-[10px] font-bold uppercase tracking-wider text-gray-400">Janus says</span>
          <p data-testid="proposal-rationale" className="mt-1 whitespace-pre-wrap break-words text-sm text-gray-700 dark:text-gray-300">
            {proposal.proposal_rationale}
          </p>
        </div>
      )}

      <SupersedeDiff proposal={proposal} tools={tools} catalog={catalog} />

      <fieldset>
        <legend className="text-[10px] font-bold uppercase tracking-wider text-gray-400">Tools</legend>
        <ul className="mt-1 space-y-1.5">
          {proposal.tool_ids.map((toolId) =>
            blocked.has(toolId) ? (
              <li key={toolId} data-blocked="true" className="rounded-lg border border-amber-400 bg-amber-50 p-2 text-sm dark:border-amber-700 dark:bg-amber-900/20">
                <label className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={allowedBlocked.has(toolId)}
                    onChange={(event) => toggleBlocked(toolId, event.target.checked)}
                    aria-label={`Allow ${toolName(tools, toolId)}`}
                  />
                  <span>
                    <span className="font-medium">{toolName(tools, toolId)}</span>{' '}
                    <span className="text-[10px] uppercase text-amber-600 dark:text-amber-400">off for unattended runs</span>
                    <span className="block text-xs text-amber-800 dark:text-amber-200">
                      Approving lets this run use it without asking. Leave it unticked to drop it.
                    </span>
                  </span>
                </label>
              </li>
            ) : (
              <li key={toolId} className="flex items-center gap-2 text-sm">
                <Check size={14} className="text-green-500" />
                {toolName(tools, toolId)}
              </li>
            )
          )}
          {proposal.tool_ids.length === 0 && <li className="text-sm text-gray-500">No tools</li>}
        </ul>
      </fieldset>

      {error && <p role="alert" className="text-sm text-red-500">{error}</p>}

      {rejecting ? (
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            onReject(proposal, reason);
          }}
        >
          <label className="block text-xs font-bold uppercase tracking-wider text-gray-400" htmlFor={`proposal-reject-${proposal.id}`}>
            Reason (optional, Janus will see it)
          </label>
          <textarea
            id={`proposal-reject-${proposal.id}`}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={2}
            className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
          />
          <div className="flex gap-2">
            <Button type="submit" size="sm" variant="danger" isLoading={busy}>Confirm reject</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setRejecting(false)}>Cancel</Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs">
            <input type="checkbox" checked={enableNow} onChange={(event) => setEnableNow(event.target.checked)} />
            Enable now
          </label>
          <Button
            size="sm"
            icon={<Check size={14} />}
            isLoading={busy}
            onClick={() => onApprove(proposal, { tool_ids: approvedToolIds, enable: enableNow })}
          >
            Approve
          </Button>
          <Button size="sm" variant="outline" icon={<Pencil size={14} />} onClick={() => onEdit(proposal, approvedToolIds)}>
            Edit then approve
          </Button>
          <Button size="sm" variant="ghost" icon={<X size={14} />} onClick={() => setRejecting(true)}>
            Reject
          </Button>
        </div>
      )}
    </article>
  );
};

const STATUS_VARIANT = {
  approved: 'success',
  rejected: 'danger',
  withdrawn: 'gray',
  superseded: 'gray',
  proposed: 'warning',
} as const;

const ProposalHistory: React.FC<{ items: TriggerProposal[] }> = ({ items }) => {
  if (items.length === 0) return null;
  return (
    <details className="rounded-xl border border-gray-200 p-3 text-sm dark:border-gray-800">
      <summary className="cursor-pointer text-xs font-bold uppercase tracking-wider text-gray-400">History</summary>
      <ul className="mt-2 space-y-1.5">
        {items.map((item) => (
          <li key={item.id} className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{item.name}</span>
            <Badge variant={STATUS_VARIANT[item.proposal_status]}>{item.proposal_status}</Badge>
            {item.rejection_reason && <span className="text-xs text-gray-500">“{item.rejection_reason}”</span>}
          </li>
        ))}
      </ul>
    </details>
  );
};

const draftFor = (proposal: TriggerProposal, toolIds: string[]): TriggerDefinitionInput => ({
  name: proposal.name,
  objective: proposal.objective,
  tool_ids: toolIds,
  kind: proposal.kind,
  schedule: proposal.schedule ? { ...proposal.schedule } : null,
  timezone: proposal.timezone,
  event_type: proposal.event_type,
  event_filter: proposal.event_filter ?? null, max_runs_per_hour: proposal.max_runs_per_hour ?? null, // JAN-363
  enabled: true,
  llm_provider: proposal.llm_provider,
  llm_model: proposal.llm_model,
});

export const SuggestedAutomations: React.FC = () => {
  const queryClient = useQueryClient();
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [editing, setEditing] = useState<{ proposal: TriggerProposal; draft: TriggerDefinitionInput } | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const { data: open = [] } = useQuery({
    queryKey: [...triggerProposalQueryKey, 'proposed'],
    queryFn: async () => (await triggerDefinitionApi.proposals('proposed')).data,
    refetchInterval: 30000,
  });
  const { data: all = [] } = useQuery({
    queryKey: [...triggerProposalQueryKey, 'all'],
    queryFn: async () => (await triggerDefinitionApi.proposals(undefined, 20)).data,
  });
  const { data: registry } = useQuery({
    queryKey: ['mcp-registry'],
    queryFn: async () => (await mcpApi.getRegistry()).data,
  });
  const { data: eventCatalog = [] } = useQuery({
    queryKey: triggerEventCatalogQueryKey,
    queryFn: async () => (await triggerDefinitionApi.eventCatalog()).data,
  });
  const tools = new Map((registry?.builtins ?? []).map((tool) => [tool.id, tool] as const));
  const history = all.filter((item) => item.proposal_status !== 'proposed').slice(0, HISTORY_SIZE);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: triggerProposalQueryKey });
    queryClient.invalidateQueries({ queryKey: triggerDefinitionQueryKey });
  };
  const fail = (id: number, error: unknown, fallback: string) =>
    setErrors((current) => ({ ...current, [id]: errorDetail(error, fallback) }));
  const approveMutation = useMutation({
    mutationFn: ({ id, approval }: { id: number; approval: TriggerProposalApproval }) =>
      triggerDefinitionApi.approveProposal(id, approval),
    onSuccess: () => {
      setEditing(null);
      setEditError(null);
      refresh();
    },
    onError: (error, { id }) => {
      if (editing) setEditError(errorDetail(error, 'Could not approve the suggestion.'));
      else fail(id, error, 'Could not approve the suggestion.');
    },
  });
  const rejectMutation = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) => triggerDefinitionApi.rejectProposal(id, reason),
    onSuccess: refresh,
    onError: (error, { id }) => fail(id, error, 'Could not reject the suggestion.'),
  });

  if (open.length === 0 && history.length === 0) return null;
  const busyId = approveMutation.isPending ? approveMutation.variables?.id : rejectMutation.isPending ? rejectMutation.variables?.id : null;

  return (
    <Card>
      <CardHeader>
        <h3 className="flex items-center gap-2 font-bold">
          <Sparkles size={18} className="text-amber-500" /> Suggested automations
          {open.length > 0 && <Badge variant="warning">{open.length}</Badge>}
        </h3>
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
          Janus drafted these from your chats. Nothing here runs until you approve it, and approving confirms exactly the
          tools you leave ticked.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {open.length === 0 && <p className="text-sm text-gray-500 dark:text-gray-400">No suggestions await review.</p>}
        {open.map((proposal) => (
          <ProposalCard
            key={proposal.id}
            proposal={proposal}
            tools={tools}
            catalog={eventCatalog}
            busy={busyId === proposal.id}
            error={errors[proposal.id] ?? null}
            onApprove={(item, approval) => approveMutation.mutate({ id: item.id, approval })}
            onEdit={(item, toolIds) => {
              setEditError(null);
              setEditing({ proposal: item, draft: draftFor(item, toolIds) });
            }}
            onReject={(item, reason) => rejectMutation.mutate({ id: item.id, reason })}
          />
        ))}
        <ProposalHistory items={history} />
      </CardContent>
      <Modal isOpen={editing !== null} onClose={() => setEditing(null)} title="Edit then approve" className="max-w-3xl">
        {editing && (
          <DefinitionEditor
            key={editing.proposal.id}
            initial={editing.draft}
            builtin={false}
            userAuthored
            tools={registry?.builtins ?? []}
            eventCatalog={eventCatalog}
            isSaving={approveMutation.isPending}
            error={editError}
            submitLabel="Save and approve"
            onCancel={() => setEditing(null)}
            onSave={({ enabled, ...draft }) =>
              approveMutation.mutate({ id: editing.proposal.id, approval: { ...draft, enable: enabled ?? true } })
            }
          />
        )}
      </Modal>
    </Card>
  );
};
