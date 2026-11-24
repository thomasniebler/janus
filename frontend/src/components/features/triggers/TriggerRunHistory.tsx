import React, { useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ChevronLeft, ChevronRight, ListChecks, RefreshCw } from 'lucide-react';
import { taskDetailPath } from '../../../lib/runtimeTasks';
import { runtimeTriggerApi } from '../../../api';
import type { RuntimeTriggerKind, RuntimeTriggerQuery, RuntimeTriggerStatus } from '../../../types';
import { Button } from '../../ui/Button';
import { Card, CardContent, CardHeader } from '../../ui/Card';
import { Input } from '../../ui/Input';
import { usePageActions } from '../../../hooks/useUIActions';
import { useTriggerDefinitions } from './triggerDefinitionQueries';
import { KIND_PRESENTATION, RUNTIME_TRIGGER_KINDS, RUNTIME_TRIGGER_STATUSES, STATUS_PRESENTATION } from './triggerPresentation';
import { TriggerWidgetList } from './TriggerWidgetList';

export const RUNS_PAGE_SIZE = 20;

/** Value of the definition filter that selects runs without a definition. */
const AD_HOC = 'none';

interface RunFilters {
  status: RuntimeTriggerStatus | '';
  kind: RuntimeTriggerKind | '';
  /** A definition id as a string, `'none'` for ad-hoc runs, or `''` for all. */
  definitionId: string;
  /** `datetime-local` input values, interpreted in the browser's local time zone. */
  start: string;
  end: string;
}

const EMPTY_FILTERS: RunFilters = { status: '', kind: '', definitionId: '', start: '', end: '' };

const selectClassName =
  'block w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-gray-700 rounded-xl outline-none text-sm';
const labelClassName = 'text-xs font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider ml-1';

/** The run filters Janus may set from chat (JAN-251); `all` clears one. */
const runFilterActions = (
  filters: RunFilters,
  updateFilter: <K extends keyof RunFilters>(key: K, value: RunFilters[K]) => void
) => ({
  page: 'triggers',
  sections: ['trigger_runs'],
  filters: {
    run_status: {
      values: ['all', ...RUNTIME_TRIGGER_STATUSES],
      get: () => filters.status || 'all',
      set: (value: string) => updateFilter('status', value === 'all' ? '' : (value as RuntimeTriggerStatus)),
    },
    run_kind: {
      values: ['all', ...RUNTIME_TRIGGER_KINDS],
      get: () => filters.kind || 'all',
      set: (value: string) => updateFilter('kind', value === 'all' ? '' : (value as RuntimeTriggerKind)),
    },
  },
});

/** Opens a run's persisted plan steps and tool trace on Agent Tasks (JAN-204). */
const renderRunActions = (trigger: { trigger_id: string }) => (
  <Link
    to={taskDetailPath({ kind: 'trigger', ref_id: trigger.trigger_id })}
    className="inline-flex items-center gap-1 font-medium text-primary hover:underline"
    data-testid="trigger-run-steps-link"
  >
    <ListChecks size={12} aria-hidden /> Plan steps and tool trace
  </Link>
);

const toIso = (localValue: string): string | undefined => (localValue ? new Date(localValue).toISOString() : undefined);

const buildQuery = (filters: RunFilters, page: number): RuntimeTriggerQuery => ({
  status: filters.status || undefined,
  kind: filters.kind || undefined,
  definition_id: filters.definitionId || undefined,
  start: toIso(filters.start),
  end: toIso(filters.end),
  limit: RUNS_PAGE_SIZE,
  offset: page * RUNS_PAGE_SIZE,
});

export interface TriggerRunHistoryProps {
  /** Called when a run's definition name is clicked. */
  onSelectDefinition?: (definitionId: number) => void;
}

/** Filterable, paginated history of every trigger run (JAN-256). */
export const TriggerRunHistory: React.FC<TriggerRunHistoryProps> = ({ onSelectDefinition }) => {
  const [filters, setFilters] = useState<RunFilters>(EMPTY_FILTERS);
  const [page, setPage] = useState(0);
  const rangeInvalid = Boolean(filters.start && filters.end && new Date(filters.start) >= new Date(filters.end));
  const query = buildQuery(filters, page);

  const { data, isLoading, isFetching, isError } = useQuery({
    queryKey: ['runtime-triggers', query],
    queryFn: async () => (await runtimeTriggerApi.list(query)).data,
    enabled: !rangeInvalid,
    placeholderData: keepPreviousData,
    // Only the newest page polls: offset pagination shifts rows as new runs arrive.
    refetchInterval: page === 0 ? 5000 : false,
  });
  const { data: definitions = [] } = useTriggerDefinitions();

  const updateFilter = <K extends keyof RunFilters>(key: K, value: RunFilters[K]) => {
    setFilters((current) => ({ ...current, [key]: value }));
    setPage(0);
  };
  usePageActions(runFilterActions(filters, updateFilter));
  const hasFilters = Object.values(filters).some(Boolean);
  const runs = data?.items ?? [];
  const total = data?.total ?? 0;
  const firstShown = total === 0 ? 0 : page * RUNS_PAGE_SIZE + 1;
  const lastShown = page * RUNS_PAGE_SIZE + runs.length;

  return (
    <Card data-ui-section="trigger_runs">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 className="font-bold">Recent runs</h3>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              All of your trigger runs, newest first. Click a run's name to jump to its definition.
            </p>
          </div>
          {isFetching && !isLoading && <RefreshCw size={14} className="animate-spin text-primary" aria-label="Refreshing" />}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5" data-testid="trigger-run-filters">
          <label className="space-y-1">
            <span className={labelClassName}>Status</span>
            <select
              aria-label="Filter runs by status"
              className={selectClassName}
              value={filters.status}
              onChange={(event) => updateFilter('status', event.target.value as RunFilters['status'])}
            >
              <option value="">All statuses</option>
              {RUNTIME_TRIGGER_STATUSES.map((status) => (
                <option key={status} value={status}>{STATUS_PRESENTATION[status].label}</option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className={labelClassName}>Kind</span>
            <select
              aria-label="Filter runs by kind"
              className={selectClassName}
              value={filters.kind}
              onChange={(event) => updateFilter('kind', event.target.value as RunFilters['kind'])}
            >
              <option value="">All kinds</option>
              {RUNTIME_TRIGGER_KINDS.map((kind) => (
                <option key={kind} value={kind}>{KIND_PRESENTATION[kind].label}</option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className={labelClassName}>Definition</span>
            <select
              aria-label="Filter runs by definition"
              className={selectClassName}
              value={filters.definitionId}
              onChange={(event) => updateFilter('definitionId', event.target.value)}
            >
              <option value="">All definitions</option>
              <option value={AD_HOC}>Ad-hoc runs (no definition)</option>
              {definitions.map((definition) => (
                <option key={definition.id} value={String(definition.id)}>{definition.name}</option>
              ))}
            </select>
          </label>
          <Input
            label="From"
            type="datetime-local"
            value={filters.start}
            onChange={(event) => updateFilter('start', event.target.value)}
          />
          <Input
            label="Until"
            type="datetime-local"
            value={filters.end}
            onChange={(event) => updateFilter('end', event.target.value)}
          />
        </div>
        {hasFilters && (
          <button type="button" className="text-xs font-medium text-primary hover:underline" onClick={() => { setFilters(EMPTY_FILTERS); setPage(0); }}>
            Clear filters
          </button>
        )}
        {rangeInvalid && <p className="text-sm text-red-500">"From" must be before "Until".</p>}

        {isLoading && <RefreshCw className="animate-spin text-primary" />}
        {isError && (
          <p className="text-sm text-red-300">Runtime trigger API is unavailable. Apply the backend migration and release before using this panel.</p>
        )}
        {!isLoading && !isError && !rangeInvalid && runs.length === 0 && (
          <p className="text-sm text-gray-500 dark:text-gray-400">{hasFilters ? 'No runs match these filters.' : 'No triggers yet.'}</p>
        )}
        {!rangeInvalid && <TriggerWidgetList triggers={runs} widgetStyle="card" onSelectDefinition={onSelectDefinition} renderActions={renderRunActions} />}

        {total > 0 && !rangeInvalid && (
          <div className="flex items-center justify-between gap-3 text-xs text-gray-500 dark:text-gray-400" data-testid="trigger-run-pagination">
            <span>
              {firstShown}–{lastShown} of {total}
            </span>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" icon={<ChevronLeft size={14} />} disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>
                Newer
              </Button>
              <Button size="sm" variant="outline" icon={<ChevronRight size={14} />} disabled={!data?.has_more} onClick={() => setPage((value) => value + 1)}>
                Older
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
};
