import { formatServerDateTime } from '../lib/serverTime';
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, AlertTriangle, Clock, ChevronDown, ChevronRight, Trash2, PlayCircle } from 'lucide-react';
import { errorApi } from '../api';
import type { ErrorGroup } from '../types';
import { Button } from '../components/ui/Button';

const ErrorsPage: React.FC = () => {
  const [expandedGroups, setExpandedGroups] = useState<Set<number>>(new Set());
  const queryClient = useQueryClient();

  const { data: errorGroups, isLoading, error } = useQuery({
    queryKey: ['error-groups'],
    queryFn: async () => {
      const response = await errorApi.listGroups({ limit: 100 });
      return response.data;
    },
    refetchInterval: 30000,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => errorApi.deleteGroup(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['error-groups'] });
    },
  });

  const compactMutation = useMutation({
    mutationFn: () => errorApi.compact(28),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['error-groups'] });
    },
  });

  const toggleExpanded = (id: number) => {
    setExpandedGroups(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const formatTimestamp = (timestamp: string) => {
    return formatServerDateTime(timestamp) ?? timestamp;
  };

  const parseSampleDetails = (jsonString: string | null | undefined): Record<string, unknown> | null => {
    if (!jsonString) return null;
    try {
      return JSON.parse(jsonString);
    } catch {
      return null;
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary"></div>
      </div>
    );
  }

  if (error || !errorGroups) {
    return (
      <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl p-6 text-center">
        <p className="text-red-600 dark:text-red-400 font-medium">Failed to load error groups.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
            <Activity className="text-primary" />
            Error Groups
          </h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">
            Grouped error events with occurrence counts and sample details.
          </p>
        </div>
        <Button
          onClick={() => compactMutation.mutate()}
          disabled={compactMutation.isPending}
          variant="primary"
          className="flex items-center gap-2"
        >
          <PlayCircle size={16} />
          {compactMutation.isPending ? 'Compacting...' : 'Compact Now'}
        </Button>
      </div>

      {errorGroups.length === 0 ? (
        <div className="bg-white dark:bg-dark-card rounded-2xl shadow-sm border border-gray-100 dark:border-gray-800 p-12 text-center">
          <p className="text-gray-500 dark:text-gray-400">No error groups found. 🎉</p>
        </div>
      ) : (
        <div className="space-y-3">
          {errorGroups.map((group: ErrorGroup) => {
            const isExpanded = expandedGroups.has(group.id);
            const sampleDetails = parseSampleDetails(group.sample_details);

            return (
              <div
                key={group.id}
                className="bg-white dark:bg-dark-card rounded-xl shadow-sm border border-gray-100 dark:border-gray-800 overflow-hidden hover:border-gray-200 dark:hover:border-gray-700 transition-colors"
              >
                <div className="p-4 flex items-start gap-4">
                  <button
                    type="button"
                    onClick={() => toggleExpanded(group.id)}
                    className="mt-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors"
                    aria-label={`${isExpanded ? 'Collapse' : 'Expand'} error group details`}
                    aria-expanded={isExpanded}
                    aria-controls={`error-group-details-${group.id}`}
                  >
                    {isExpanded ? <ChevronDown size={20} /> : <ChevronRight size={20} />}
                  </button>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-2">
                          <AlertTriangle size={16} className="text-red-500 flex-shrink-0" />
                          <span className="px-2 py-1 rounded bg-gray-100 dark:bg-gray-800 text-[10px] font-bold text-gray-600 dark:text-gray-400 border border-gray-200 dark:border-gray-700 uppercase">
                            {group.source}
                          </span>
                          <span className="px-2 py-1 rounded bg-red-100 dark:bg-red-900/30 text-[10px] font-bold text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800">
                            {group.occurrence_count} occurrence{group.occurrence_count !== 1 ? 's' : ''}
                          </span>
                        </div>
                        <p className="text-sm font-medium leading-relaxed break-words">
                          {group.message_template}
                        </p>
                        <div className="flex items-center gap-4 mt-2 text-xs text-gray-400">
                          <div className="flex items-center gap-1">
                            <Clock size={12} />
                            First: {formatTimestamp(group.first_seen)}
                          </div>
                          <div className="flex items-center gap-1">
                            <Clock size={12} />
                            Last: {formatTimestamp(group.last_seen)}
                          </div>
                        </div>
                      </div>

                      <button
                        type="button"
                        onClick={() => deleteMutation.mutate(group.id)}
                        disabled={deleteMutation.isPending}
                        className="text-red-400 hover:text-red-600 dark:hover:text-red-300 transition-colors flex-shrink-0"
                        aria-label="Delete error group"
                        title="Delete error group"
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>

                    {isExpanded && sampleDetails && (
                      <div
                        id={`error-group-details-${group.id}`}
                        className="mt-4 p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg border border-gray-200 dark:border-gray-700"
                      >
                        <div className="text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-2">
                          Sample Details
                        </div>
                        <pre className="text-xs text-gray-700 dark:text-gray-300 overflow-x-auto">
                          {JSON.stringify(sampleDetails, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default ErrorsPage;
