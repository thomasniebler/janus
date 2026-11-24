import { formatServerDateTime } from '../lib/serverTime';
import React, { useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Activity, Clock, Info, AlertTriangle, CheckCircle, XCircle, ChevronLeft, ChevronRight } from 'lucide-react';
import { dashboardApi } from '../api';
import { Button } from '../components/ui/Button';
import { Input } from '../components/ui/Input';
import type { SystemEvent, SystemEventQuery, SystemEventType } from '../types';

const EVENTS_PAGE_SIZE = 50;

const EVENT_TYPES: SystemEventType[] = ['INFO', 'SUCCESS', 'WARNING', 'ERROR'];

interface EventFilters {
  eventType: SystemEventType | '';
  source: string;
  /** `datetime-local` input values, interpreted in the browser's local time zone. */
  start: string;
  end: string;
}

const EMPTY_FILTERS: EventFilters = { eventType: '', source: '', start: '', end: '' };

const selectClassName =
  'block w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-gray-700 rounded-xl outline-none text-sm';
const labelClassName = 'text-xs font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider ml-1';

const toIso = (localValue: string): string | undefined =>
  localValue ? new Date(localValue).toISOString() : undefined;

const buildQuery = (filters: EventFilters, page: number): SystemEventQuery => ({
  event_type: filters.eventType || undefined,
  source: filters.source || undefined,
  start: toIso(filters.start),
  end: toIso(filters.end),
  limit: EVENTS_PAGE_SIZE,
  offset: page * EVENTS_PAGE_SIZE,
});

const getEventIcon = (type: string) => {
  switch (type.toLowerCase()) {
    case 'success': return <CheckCircle size={16} className="text-green-500" />;
    case 'error': return <XCircle size={16} className="text-red-500" />;
    case 'warning': return <AlertTriangle size={16} className="text-yellow-500" />;
    default: return <Info size={16} className="text-blue-500" />;
  }
};

const Events: React.FC = () => {
  const [filters, setFilters] = useState<EventFilters>(EMPTY_FILTERS);
  const [page, setPage] = useState(0);

  const rangeInvalid = Boolean(filters.start && filters.end && new Date(filters.start) >= new Date(filters.end));
  const query = buildQuery(filters, page);

  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ['events', query],
    queryFn: async () => {
      const response = await dashboardApi.getEvents(query);
      return response.data;
    },
    enabled: !rangeInvalid,
    placeholderData: keepPreviousData,
    refetchInterval: page === 0 ? 10000 : false, // Only poll newest-first page; offset-based pagination shifts rows
  });

  const { data: sources } = useQuery({
    queryKey: ['events', 'sources'],
    queryFn: async () => {
      const response = await dashboardApi.getEventSources();
      return response.data;
    },
    staleTime: 60000,
  });

  const updateFilter = <K extends keyof EventFilters>(key: K, value: EventFilters[K]) => {
    setFilters((current) => ({ ...current, [key]: value }));
    setPage(0);
  };

  const hasFilters = Object.values(filters).some(Boolean);
  const events = data?.items ?? [];
  const firstShown = data && data.total > 0 ? data.offset + 1 : 0;
  const lastShown = data ? data.offset + data.items.length : 0;

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
          <Activity className="text-primary" />
          System Events
        </h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">Real-time log of background activities and processes.</p>
      </div>

      <div className="bg-white dark:bg-dark-card rounded-2xl shadow-sm border border-gray-100 dark:border-gray-800 p-4">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 items-end">
          <div className="space-y-1.5">
            <label className={labelClassName} htmlFor="events-filter-type">Type</label>
            <select
              id="events-filter-type"
              value={filters.eventType}
              onChange={(event) => updateFilter('eventType', event.target.value as SystemEventType | '')}
              className={selectClassName}
            >
              <option value="">All types</option>
              {EVENT_TYPES.map((type) => (
                <option key={type} value={type}>{type.charAt(0) + type.slice(1).toLowerCase()}</option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <label className={labelClassName} htmlFor="events-filter-source">Source</label>
            <select
              id="events-filter-source"
              value={filters.source}
              onChange={(event) => updateFilter('source', event.target.value)}
              className={selectClassName}
            >
              <option value="">All sources</option>
              {(sources ?? []).map((source) => (
                <option key={source} value={source}>{source}</option>
              ))}
            </select>
          </div>
          <Input
            label="From"
            type="datetime-local"
            value={filters.start}
            onChange={(event) => updateFilter('start', event.target.value)}
          />
          <Input
            label="To"
            type="datetime-local"
            value={filters.end}
            onChange={(event) => updateFilter('end', event.target.value)}
            error={rangeInvalid ? 'Must be after From' : undefined}
          />
        </div>
        {hasFilters && (
          <div className="mt-3 flex justify-end">
            <Button variant="ghost" size="sm" onClick={() => { setFilters(EMPTY_FILTERS); setPage(0); }}>
              Clear filters
            </Button>
          </div>
        )}
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center h-64">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary"></div>
        </div>
      ) : error ? (
        <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl p-6 text-center">
          <p className="text-red-600 dark:text-red-400 font-medium">Failed to load system events.</p>
        </div>
      ) : (
        <div className="bg-white dark:bg-dark-card rounded-2xl shadow-sm border border-gray-100 dark:border-gray-800 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-gray-50 dark:bg-gray-800/50 text-gray-400 text-xs font-bold uppercase tracking-wider">
                  <th className="px-6 py-4">Time</th>
                  <th className="px-6 py-4">Source</th>
                  <th className="px-6 py-4">Message</th>
                  <th className="px-6 py-4">Type</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                {events.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-6 py-10 text-center text-sm text-gray-500 dark:text-gray-400">
                      {hasFilters ? 'No events match these filters.' : 'No events recorded yet.'}
                    </td>
                  </tr>
                )}
                {events.map((event: SystemEvent) => (
                  <tr key={event.id} className="hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors">
                    <td className="px-6 py-4 whitespace-nowrap text-xs text-gray-400">
                      <div className="flex items-center gap-2">
                        <Clock size={12} />
                        {(event.timestamp && formatServerDateTime(event.timestamp)) || event.timestamp || '—'}
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className="px-2 py-1 rounded bg-gray-100 dark:bg-gray-800 text-[10px] font-bold text-gray-600 dark:text-gray-400 border border-gray-200 dark:border-gray-700 uppercase">
                        {event.source}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <p className="text-sm font-medium leading-relaxed">{event.message}</p>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center gap-2 text-xs font-medium capitalize">
                        {getEventIcon(event.event_type ?? 'info')}
                        {event.event_type?.toLowerCase()}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data && (
            <div className="flex items-center justify-between gap-4 px-6 py-3 border-t border-gray-100 dark:border-gray-800 text-xs text-gray-500 dark:text-gray-400">
              <span aria-live="polite">
                {data.total === 0 ? '0 events' : `${firstShown}–${lastShown} of ${data.total}`}
                {isFetching && <span className="ml-2 text-gray-400">Refreshing…</span>}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  aria-label="Previous page"
                  icon={<ChevronLeft size={14} />}
                  disabled={page === 0}
                  onClick={() => setPage((current) => Math.max(0, current - 1))}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  aria-label="Next page"
                  icon={<ChevronRight size={14} />}
                  disabled={!data.has_more}
                  onClick={() => setPage((current) => current + 1)}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default Events;
