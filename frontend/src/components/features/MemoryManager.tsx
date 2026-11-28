import { formatServerDateTime } from '../../lib/serverTime';
import React, { useId, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Brain, Database, Pencil, Plus, RefreshCw, Search, Sparkles, Trash2 } from 'lucide-react';
import { memoryApi } from '../../api';
import type { AgentMemoryCompactionRun, AgentMemoryFact, AgentMemoryPin, AgentMemoryVerification } from '../../types';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';
import { Input } from '../ui/Input';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import { useMemoryFactsPageContext } from '../../hooks/pageContextSections';

const TEXTAREA_CLASS =
  'w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 resize-y';

const errorMessage = (error: unknown): string => {
  const detail = (error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail;
  return typeof detail === 'string' && detail ? detail : 'Could not save the memory fact.';
};

/** Inline editor for one fact's summary and detail (JAN-224). */
const MemoryFactEditor: React.FC<{ fact: AgentMemoryFact; onDone: () => void }> = ({ fact, onDone }) => {
  const queryClient = useQueryClient();
  const detailId = useId();
  const [summary, setSummary] = useState(fact.summary);
  const [detail, setDetail] = useState(fact.detail ?? '');
  const updateMutation = useMutation({
    mutationFn: () => memoryApi.update(fact.id, { summary, detail }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['memory-facts'] });
      onDone();
    },
  });

  return (
    <form
      className="space-y-3"
      aria-label={`Edit memory fact "${fact.summary}"`}
      onSubmit={(event) => {
        event.preventDefault();
        updateMutation.mutate();
      }}
    >
      <Input label="Summary" value={summary} onChange={(event) => setSummary(event.target.value)} />
      <div className="space-y-1">
        <label htmlFor={detailId} className="block text-sm font-medium text-gray-700 dark:text-gray-300">
          Detail
        </label>
        <textarea
          id={detailId}
          value={detail}
          onChange={(event) => setDetail(event.target.value)}
          rows={3}
          className={TEXTAREA_CLASS}
        />
      </div>
      {updateMutation.isError && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {errorMessage(updateMutation.error)}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" size="sm" isLoading={updateMutation.isPending} disabled={!summary.trim()}>
          Save changes
        </Button>
      </div>
    </form>
  );
};

const VERIFICATION_LABELS: Record<AgentMemoryVerification, { title: string; className: string }> = {
  unverified: {
    title: 'Not checked yet. The nightly memory review checks facts Janus wrote against the conversation they came from.',
    className: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
  },
  confirmed: {
    title: 'You wrote it, or its conversation supports it as written.',
    className: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  },
  corrected: {
    title: 'The memory review rewrote it to match its conversation.',
    className: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  },
  unsupported: {
    title: 'Its conversation does not mention it; it is kept but never added to the memory index automatically.',
    className: 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300',
  },
  contradicted: {
    title: 'Its conversation says otherwise; the memory review archived it.',
    className: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  },
};

const PIN_OPTIONS: { value: '' | AgentMemoryPin; label: string }[] = [
  { value: '', label: 'Index: automatic' },
  { value: 'pinned', label: 'Index: pinned' },
  { value: 'hidden', label: 'Index: hidden' },
];

/** One stored fact: summary, detail, origin, status and verification, with its index, edit and delete controls. */
const MemoryFactView: React.FC<{
  fact: AgentMemoryFact;
  onEdit: () => void;
  onDelete: () => void;
  onPin: (pin: AgentMemoryPin | null) => void;
  isDeleting: boolean;
}> = ({ fact, onEdit, onDelete, onPin, isDeleting }) => (
  <>
    <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-3">
      <div className="min-w-0">
        <p className="font-semibold text-sm text-gray-900 dark:text-gray-100">{fact.summary}</p>
        {fact.detail && <p className="mt-1 text-sm text-gray-600 dark:text-gray-300 whitespace-pre-wrap">{fact.detail}</p>}
        {fact.evidence_quote && (
          <blockquote className="mt-2 border-l-2 border-gray-200 dark:border-gray-700 pl-3 text-xs text-gray-500 dark:text-gray-400 whitespace-pre-wrap">
            <span className="font-medium">Evidence:</span> “{fact.evidence_quote}”
          </blockquote>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-full bg-primary/10 px-2 py-1 font-medium text-primary">{fact.source}</span>
        <span
          className={`rounded-full px-2 py-1 font-medium ${VERIFICATION_LABELS[fact.verification ?? 'unverified'].className}`}
          title={VERIFICATION_LABELS[fact.verification ?? 'unverified'].title}
        >
          {fact.verification ?? 'unverified'}
        </span>
        {fact.in_memory_index && fact.status === 'active' && (
          <span
            className="rounded-full bg-primary/10 px-2 py-1 font-medium text-primary"
            title="In the memory index Janus sees at the start of every conversation and run."
          >
            in index
          </span>
        )}
        <span
          className={`rounded-full px-2 py-1 font-medium ${
            fact.status === 'active'
              ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
              : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'
          }`}
        >
          {fact.status}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onEdit}
          aria-label={`Edit memory fact "${fact.summary}"`}
          icon={<Pencil size={15} />}
        />
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onDelete}
          isLoading={isDeleting}
          aria-label={`Delete memory fact "${fact.summary}"`}
          icon={<Trash2 size={15} className="text-red-500" />}
        />
        <select
          value={fact.pin ?? ''}
          onChange={(event) => onPin((event.target.value || null) as AgentMemoryPin | null)}
          aria-label={`Memory index for "${fact.summary}"`}
          className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 text-xs"
        >
          {PIN_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
    </div>
    <p className="mt-3 text-xs text-gray-400 dark:text-gray-500">
      Updated {formatServerDateTime(fact.updated_at) ?? fact.updated_at}
      {fact.verified_at && <> · Checked {formatServerDateTime(fact.verified_at) ?? fact.verified_at}</>}
    </p>
  </>
);

export const MemoryManager: React.FC = () => {
  const queryClient = useQueryClient();
  const { confirm, dialog } = useConfirmDialog();
  const [query, setQuery] = useState('');
  const [includeArchived, setIncludeArchived] = useState(false);
  const [summary, setSummary] = useState('');
  const [detail, setDetail] = useState('');
  // The fact being edited is kept as a snapshot: if it drops out of the list while the
  // editor is open (deleted elsewhere, or filtered out), the editor and its unsaved text
  // stay on screen with the save error instead of vanishing.
  const [editingFact, setEditingFact] = useState<{ fact: AgentMemoryFact; index: number } | null>(null);
  const detailId = useId();

  const { data: facts = [], isLoading: factsLoading } = useQuery<AgentMemoryFact[]>({
    queryKey: ['memory-facts', query, includeArchived],
    queryFn: async () => (await memoryApi.list({ query, includeArchived })).data,
  });

  useMemoryFactsPageContext(facts, query, includeArchived); // what Janus sees in chat (JAN-250)

  const { data: latestRun } = useQuery<AgentMemoryCompactionRun | null>({
    queryKey: ['memory-compaction-latest'],
    queryFn: async () => (await memoryApi.getLatestCompactionRun()).data,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'queued' || status === 'running' ? 2000 : false;
    },
  });

  const createMutation = useMutation({
    mutationFn: () => memoryApi.create({ summary, detail: detail || null }),
    onSuccess: () => {
      setSummary('');
      setDetail('');
      queryClient.invalidateQueries({ queryKey: ['memory-facts'] });
    },
  });

  const compactMutation = useMutation({
    mutationFn: () => memoryApi.compact(),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['memory-compaction-latest'] }),
  });

  const pinMutation = useMutation({
    mutationFn: ({ id, pin }: { id: number; pin: AgentMemoryPin | null }) => memoryApi.setPin(id, pin),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['memory-facts'] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => memoryApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['memory-facts'] });
    },
  });

  // Same parent, same key: React keeps the open editor's state when its fact leaves `facts`.
  const rows =
    editingFact && !facts.some((fact) => fact.id === editingFact.fact.id)
      ? [...facts.slice(0, editingFact.index), editingFact.fact, ...facts.slice(editingFact.index)]
      : facts;
  const isCompacting = latestRun?.status === 'queued' || latestRun?.status === 'running' || compactMutation.isPending;

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
            <Brain className="text-primary" />
            Agent Memory
          </h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">
            Durable facts Janus can search and reuse. The nightly Memory review checks the facts Janus wrote against the
            conversations they came from and picks the few for the memory index every conversation starts with; pin or
            hide a fact to decide yourself.
          </p>
        </div>

        <Button
          type="button"
          variant="outline"
          onClick={() => compactMutation.mutate()}
          isLoading={compactMutation.isPending}
          disabled={isCompacting}
          icon={<Sparkles size={16} />}
        >
          Compact Memory
        </Button>
      </div>

      <Card className="p-6 space-y-4">
        <div className="flex items-center gap-2">
          <Plus size={18} className="text-primary" />
          <h2 className="text-lg font-bold">Add memory fact</h2>
        </div>
        <Input
          label="Summary"
          value={summary}
          onChange={(event) => setSummary(event.target.value)}
          placeholder="e.g. I prefer deep work in the morning."
        />
        <div className="space-y-1">
          <label htmlFor={detailId} className="block text-sm font-medium text-gray-700 dark:text-gray-300">
            Detail
          </label>
          <textarea
            id={detailId}
            value={detail}
            onChange={(event) => setDetail(event.target.value)}
            rows={3}
            placeholder="Optional supporting context"
            className={TEXTAREA_CLASS}
          />
        </div>
        <div className="flex justify-end">
          <Button
            type="button"
            onClick={() => createMutation.mutate()}
            isLoading={createMutation.isPending}
            disabled={!summary.trim()}
            icon={<Database size={16} />}
          >
            Save fact
          </Button>
        </div>
      </Card>

      <Card className="p-6 space-y-3">
        <div className="flex items-center gap-2">
          <Sparkles size={18} className="text-primary" />
          <h2 className="text-lg font-bold">Compaction status</h2>
        </div>
        {latestRun ? (
          <div className="space-y-2 text-sm text-gray-600 dark:text-gray-300">
            <p>
              <span className="font-semibold text-gray-900 dark:text-gray-100">Status:</span> {latestRun.status}
            </p>
            {latestRun.result_summary && (
              <p>
                <span className="font-semibold text-gray-900 dark:text-gray-100">Summary:</span> {latestRun.result_summary}
              </p>
            )}
            {latestRun.error_message && (
              <p className="text-red-600 dark:text-red-400">
                <span className="font-semibold">Error:</span> {latestRun.error_message}
              </p>
            )}
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Considered {latestRun.facts_considered} fact(s), archived {latestRun.facts_archived}, created {latestRun.facts_created}.
            </p>
          </div>
        ) : (
          <p className="text-sm text-gray-500 dark:text-gray-400">No compaction run yet.</p>
        )}
      </Card>

      <Card className="p-6 space-y-4">
        <div className="flex flex-col md:flex-row gap-3 md:items-center md:justify-between">
          <div className="flex items-center gap-2">
            <Search size={18} className="text-primary" />
            <h2 className="text-lg font-bold">Stored facts</h2>
          </div>
          <div className="flex flex-col md:flex-row gap-3 md:items-center">
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search memory facts"
              aria-label="Search memory facts"
              className="md:w-80"
            />
            <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
              <input
                type="checkbox"
                checked={includeArchived}
                onChange={(event) => setIncludeArchived(event.target.checked)}
                className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
              />
              Show archived
            </label>
          </div>
        </div>

        {factsLoading && rows.length === 0 ? (
          <div className="flex items-center justify-center py-8">
            <RefreshCw className="animate-spin text-primary" />
          </div>
        ) : rows.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 p-8 text-center text-sm text-gray-500 dark:text-gray-400">
            No memory facts match the current filter.
          </div>
        ) : (
          <div className="space-y-3">
            {rows.map((fact, index) => (
              <div key={fact.id} className="rounded-2xl border border-gray-100 dark:border-gray-800 p-4">
                {editingFact?.fact.id === fact.id ? (
                  <MemoryFactEditor fact={fact} onDone={() => setEditingFact(null)} />
                ) : (
                  <MemoryFactView
                    fact={fact}
                    onEdit={() => setEditingFact({ fact, index })}
                    onDelete={() =>
                      confirm({
                        title: 'Delete memory fact?',
                        message: `Delete memory fact "${fact.summary}"?`,
                        confirmLabel: 'Delete',
                        tone: 'warning',
                        onConfirm: () => deleteMutation.mutate(fact.id),
                      })
                    }
                    onPin={(pin) => pinMutation.mutate({ id: fact.id, pin })}
                    isDeleting={deleteMutation.isPending && deleteMutation.variables === fact.id}
                  />
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
      {dialog}
    </div>
  );
};
