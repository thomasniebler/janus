import React, { useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, Check, Network, X } from 'lucide-react';
import { knowledgeApi } from '../../api';
import type {
  KnowledgeEntity,
  KnowledgeEntityType,
  KnowledgeExtractionStatus,
  KnowledgeMergeSuggestion,
} from '../../types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';
import { Input } from '../ui/Input';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import { KnowledgeEntityDetail } from './KnowledgeEntityDetail';
import { KnowledgeSearchPanel } from './KnowledgeSearchPanel';

const TYPE_LABELS: Record<KnowledgeEntityType, string> = {
  person: 'Person',
  organization: 'Organization',
  project: 'Project',
  place: 'Place',
  product: 'Product',
  concept: 'Concept',
};

const formatTokens = (value: number) => value.toLocaleString('en-US');

const errorMessage = (error: unknown, fallback: string) =>
  axios.isAxiosError(error) ? (error.response?.data?.detail ?? error.message) : fallback;

const cloudWarning = (model: string) =>
  `Knowledge extraction sends the text of everything Janus has ingested (mail, attachments, chats, WhatsApp, ` +
  `calendar, tasks, and uploads) to ${model}. Turn it on?`;

/** Knowledge extraction switch, monthly budget and usage (JAN-243). */
const ExtractionCard: React.FC = () => {
  const queryClient = useQueryClient();
  const [budgetDraft, setBudgetDraft] = useState<string | null>(null);
  const { confirm, dialog } = useConfirmDialog();
  const { data: status, isLoading, isError } = useQuery<KnowledgeExtractionStatus>({
    queryKey: ['knowledge-extraction'],
    queryFn: async () => (await knowledgeApi.getExtraction()).data,
  });
  const mutation = useMutation({
    mutationFn: (payload: { enabled?: boolean; monthly_token_budget?: number }) =>
      knowledgeApi.updateExtraction(payload),
    // A live-sync refetch still in flight must not overwrite the saved settings.
    onMutate: () => queryClient.cancelQueries({ queryKey: ['knowledge-extraction'] }),
    onSuccess: (response) => {
      queryClient.setQueryData(['knowledge-extraction'], response.data);
      setBudgetDraft(null);
    },
  });

  if (isLoading) return <Card className="p-6 text-sm text-gray-500">Loading extraction settings…</Card>;
  if (isError || !status) {
    return (
      <Card className="p-6">
        <p className="text-sm text-red-600 dark:text-red-400 flex items-center gap-2" role="alert">
          <AlertCircle size={16} />
          Could not load the extraction settings.
        </p>
      </Card>
    );
  }

  const onToggle = (enabled: boolean) => {
    if (enabled && status.is_cloud) {
      confirm({
        title: 'Send ingested text to a cloud model?',
        message: cloudWarning(status.model),
        confirmLabel: 'Turn on',
        tone: 'warning',
        onConfirm: () => mutation.mutate({ enabled }),
      });
      return;
    }
    mutation.mutate({ enabled });
  };
  const budgetValue = budgetDraft ?? String(status.monthly_token_budget);
  const budgetNumber = Number(budgetValue);
  const budgetValid = budgetValue.trim() !== '' && Number.isInteger(budgetNumber) && budgetNumber >= 0;
  const share = status.monthly_token_budget > 0 ? Math.min(1, status.tokens_used / status.monthly_token_budget) : 1;

  return (
    <Card className="p-6 space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-lg font-bold">Knowledge extraction</h3>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Every 30 minutes, {status.model} reads newly ingested text and records the people, organizations,
            projects, places, products, and concepts in it, and how they relate.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm font-medium shrink-0">
          <input
            type="checkbox"
            checked={status.enabled}
            disabled={mutation.isPending}
            onChange={(event) => onToggle(event.target.checked)}
          />
          {status.enabled ? 'On' : 'Off'}
        </label>
      </div>

      {status.is_cloud && (
        <p className="text-xs rounded-xl p-3 bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-300">
          {status.model} is a cloud model: with extraction on, the text of all ingested sources is sent to that
          provider. Choose an Ollama model under Settings → AI &amp; Models (utility model) to keep it local.
        </p>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <p className="text-xs font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider ml-1">
            Used in {status.period}
          </p>
          <p className="text-sm">
            {formatTokens(status.tokens_used)} of {formatTokens(status.monthly_token_budget)} tokens · {status.calls}{' '}
            calls · {formatTokens(status.pending_chunks)} chunks waiting
          </p>
          <div
            className="h-2 rounded-full bg-gray-100 dark:bg-gray-800 overflow-hidden"
            role="meter"
            aria-label="Monthly token budget used"
            aria-valuemin={0}
            aria-valuemax={status.monthly_token_budget}
            aria-valuenow={status.tokens_used}
          >
            <div className="h-full bg-primary" style={{ width: `${share * 100}%` }} />
          </div>
        </div>
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (budgetValid) mutation.mutate({ monthly_token_budget: budgetNumber });
          }}
        >
          <Input
            label="Monthly token budget"
            type="number"
            min={0}
            step={1000}
            value={budgetValue}
            onChange={(event) => setBudgetDraft(event.target.value)}
            error={budgetValid ? undefined : 'Enter a whole number of tokens.'}
          />
          <Button type="submit" disabled={!budgetValid || budgetDraft === null} isLoading={mutation.isPending}>
            Save
          </Button>
        </form>
      </div>
      {mutation.isError && (
        <p className="text-sm text-red-600 dark:text-red-400" role="alert">
          {errorMessage(mutation.error, 'Saving failed.')}
        </p>
      )}
      {dialog}
    </Card>
  );
};

const EntityName: React.FC<{ entity: KnowledgeEntity }> = ({ entity }) => (
  <span className="font-medium">
    {entity.name}{' '}
    <span className="text-xs text-gray-500 dark:text-gray-400">
      ({TYPE_LABELS[entity.entity_type] ?? entity.entity_type}
      {entity.aliases.length > 0 ? `, also ${entity.aliases.join(', ')}` : ''})
    </span>
  </span>
);

/** Merge suggestions: similar names Janus did not merge on its own. */
const MergeQueue: React.FC = () => {
  const queryClient = useQueryClient();
  const { data: suggestions = [], isError } = useQuery<KnowledgeMergeSuggestion[]>({
    queryKey: ['knowledge-merge-suggestions'],
    queryFn: async () => (await knowledgeApi.listMergeSuggestions()).data,
  });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['knowledge-merge-suggestions'] });
    queryClient.invalidateQueries({ queryKey: ['knowledge-entities'] });
  };
  const accept = useMutation({ mutationFn: (id: number) => knowledgeApi.acceptMerge(id), onSuccess: refresh });
  const reject = useMutation({ mutationFn: (id: number) => knowledgeApi.rejectMerge(id), onSuccess: refresh });

  if (isError) {
    return (
      <Card className="p-6">
        <p className="text-sm text-red-600 dark:text-red-400" role="alert">
          Could not load the merge suggestions.
        </p>
      </Card>
    );
  }
  if (suggestions.length === 0) return null;
  return (
    <Card className="p-6 space-y-3">
      <h3 className="text-lg font-bold">Possible duplicates</h3>
      <p className="text-sm text-gray-500 dark:text-gray-400">
        Janus only merges names that match exactly. These look alike; merge them or keep them apart.
      </p>
      <ul className="divide-y divide-gray-100 dark:divide-gray-800" aria-label="Merge suggestions">
        {suggestions.map((suggestion) => (
          <li key={suggestion.id} className="py-3 flex flex-col md:flex-row md:items-center justify-between gap-3">
            <div className="text-sm space-y-1">
              <p>
                Merge <EntityName entity={suggestion.duplicate} /> into <EntityName entity={suggestion.entity} />?
              </p>
              <p className="text-xs text-gray-500 dark:text-gray-400">Because {suggestion.reason}.</p>
            </div>
            <div className="flex gap-2 shrink-0">
              <Button
                type="button"
                icon={<Check size={16} />}
                isLoading={accept.isPending && accept.variables === suggestion.id}
                onClick={() => accept.mutate(suggestion.id)}
              >
                Merge
              </Button>
              <Button
                type="button"
                variant="ghost"
                icon={<X size={16} />}
                isLoading={reject.isPending && reject.variables === suggestion.id}
                onClick={() => reject.mutate(suggestion.id)}
              >
                Keep apart
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
};

/** Entities Janus extracted, most mentioned first. */
const EntityList: React.FC<{ onSelectEntity: (entityId: number) => void }> = ({ onSelectEntity }) => {
  const [typeFilter, setTypeFilter] = useState<KnowledgeEntityType | ''>('');
  const [query, setQuery] = useState('');
  const { data: entities = [], isLoading, isError } = useQuery<KnowledgeEntity[]>({
    queryKey: ['knowledge-entities', typeFilter, query],
    queryFn: async () => (await knowledgeApi.listEntities({ entityType: typeFilter || undefined, query })).data,
  });

  return (
    <Card className="p-6 space-y-4">
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <h3 className="text-lg font-bold">Entities</h3>
        <div className="flex gap-2 items-end">
          <Input label="Search" value={query} onChange={(event) => setQuery(event.target.value)} />
          <label className="text-sm text-gray-500 dark:text-gray-400 flex items-center gap-2">
            Type
            <select
              aria-label="Entity type"
              value={typeFilter}
              onChange={(event) => setTypeFilter(event.target.value as KnowledgeEntityType | '')}
              className="rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-dark-bg px-3 py-2 text-sm"
            >
              <option value="">All</option>
              {(Object.keys(TYPE_LABELS) as KnowledgeEntityType[]).map((type) => (
                <option key={type} value={type}>
                  {TYPE_LABELS[type]}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      {isLoading ? (
        <p className="text-sm text-gray-500 dark:text-gray-400">Loading entities…</p>
      ) : isError ? (
        <p className="text-sm text-red-600 dark:text-red-400" role="alert">
          Could not load the entities.
        </p>
      ) : entities.length === 0 ? (
        <p className="text-sm text-gray-500 dark:text-gray-400">
          No entities yet. Turn on knowledge extraction above; results appear after its next run.
        </p>
      ) : (
        <ul className="divide-y divide-gray-100 dark:divide-gray-800" aria-label="Entities">
          {entities.map((entity) => (
            <li key={entity.id} className="py-3 space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className="font-medium underline-offset-2 hover:underline"
                  onClick={() => onSelectEntity(entity.id)}
                >
                  {entity.name}
                </button>
                <Badge variant="gray">{TYPE_LABELS[entity.entity_type] ?? entity.entity_type}</Badge>
                <span className="text-xs text-gray-500 dark:text-gray-400">
                  {entity.mention_count} mention{entity.mention_count === 1 ? '' : 's'} · {entity.relation_count}{' '}
                  relation{entity.relation_count === 1 ? '' : 's'}
                </span>
              </div>
              {entity.aliases.length > 0 && (
                <p className="text-xs text-gray-500 dark:text-gray-400">Also: {entity.aliases.join(', ')}</p>
              )}
              {entity.description && <p className="text-sm text-gray-600 dark:text-gray-300">{entity.description}</p>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
};

/** The Knowledge tab (JAN-243): extraction settings, search, entity detail, the merge queue, and the entity list. */
export const KnowledgeGraphManager: React.FC = () => {
  const [selectedEntity, setSelectedEntity] = useState<number | null>(null);
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold tracking-tight flex items-center gap-3">
          <Network className="text-primary" />
          Knowledge
        </h2>
        <p className="text-gray-500 dark:text-gray-400 mt-1">
          The people, organizations, projects, and places in your mail, chats, and documents, and how they connect.
        </p>
      </div>
      <ExtractionCard />
      <KnowledgeSearchPanel onSelectEntity={setSelectedEntity} />
      {selectedEntity !== null && (
        <KnowledgeEntityDetail
          key={selectedEntity}
          entityId={selectedEntity}
          onSelectEntity={setSelectedEntity}
          onClose={() => setSelectedEntity(null)}
        />
      )}
      <MergeQueue />
      <EntityList onSelectEntity={setSelectedEntity} />
    </div>
  );
};
