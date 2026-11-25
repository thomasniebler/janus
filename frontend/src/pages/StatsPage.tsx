import { formatServerDateTime } from '../lib/serverTime';
import React, { useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { BarChart3, RefreshCw } from 'lucide-react';

import { statsApi } from '../api';
import { ApprovalStatsCard } from '../components/features/stats/ApprovalStatsCard';
import { LlmUsageCard } from '../components/features/stats/LlmUsageCard';
import { DailyBarChart, VIZ_STYLE, type DailySeries } from '../components/features/stats/DailyBarChart';
import { percent } from '../components/features/stats/format';
import { StatTile } from '../components/features/stats/StatTile';
import { Badge } from '../components/ui/Badge';
import { Card, CardContent, CardHeader } from '../components/ui/Card';
import type { StatsRangeDays, StatsSummary } from '../types';

const RANGES: StatsRangeDays[] = [7, 30, 90];

const browserTimezone = (): string => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
};

type MailKey = keyof StatsSummary['mail']['totals'];
type TriggerKey = keyof StatsSummary['triggers']['totals'];
type MessagingKey = keyof StatsSummary['messaging']['totals'];

const MAIL_SERIES: DailySeries<MailKey>[] = [
  { key: 'important', label: 'Important', color: 'var(--viz-1)' },
  { key: 'digest', label: 'Digest', color: 'var(--viz-2)' },
  { key: 'ignore', label: 'Ignore', color: 'var(--viz-3)' },
  { key: 'spam', label: 'Spam', color: 'var(--viz-4)' },
];
/** Run outcomes are states, so they use the reserved status palette (always paired with a label). */
const TRIGGER_SERIES: DailySeries<TriggerKey>[] = [
  { key: 'completed', label: 'Completed', color: 'var(--viz-good)' },
  { key: 'failed', label: 'Failed', color: 'var(--viz-critical)' },
  { key: 'cancelled', label: 'Cancelled', color: 'var(--viz-neutral)' },
  { key: 'pending', label: 'Queued / running', color: 'var(--viz-warning)' },
];
const MEMORY_SERIES: DailySeries<'new_facts'>[] = [{ key: 'new_facts', label: 'New memory facts', color: 'var(--viz-1)' }];
const MESSAGING_SERIES: DailySeries<MessagingKey>[] = [
  { key: 'whatsapp_in', label: 'WhatsApp received', color: 'var(--viz-1)' },
  { key: 'whatsapp_out', label: 'WhatsApp sent', color: 'var(--viz-2)' },
  { key: 'chat_user', label: 'Chat: you', color: 'var(--viz-3)' },
  { key: 'chat_assistant', label: 'Chat: Janus', color: 'var(--viz-4)' },
];

const formatDuration = (seconds: number | null): string => {
  if (seconds === null) return '—';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
};

const sum = (values: Record<string, number>): number => Object.values(values).reduce((total, value) => total + value, 0);

/** Settings → Stats: what Janus has been doing over the selected range (JAN-258). */
const StatsPage: React.FC = () => {
  const [days, setDays] = useState<StatsRangeDays>(30);
  const timezone = browserTimezone();
  const { data, isLoading, isError, isFetching } = useQuery({
    queryKey: ['stats', days, timezone],
    queryFn: async () => (await statsApi.get(days, timezone)).data,
    placeholderData: keepPreviousData,
    refetchInterval: 60000,
  });

  return (
    <div className="stats-viz space-y-6">
      <style>{VIZ_STYLE}</style>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-bold"><BarChart3 size={22} className="text-primary" /> Stats</h2>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Your mail, triggers, approvals, memory and messages per day ({timezone}). Only your own data is counted.
          </p>
        </div>
        <div className="flex items-center gap-2" role="group" aria-label="Range">
          {isFetching && !isLoading && <RefreshCw size={14} className="animate-spin text-primary" aria-label="Refreshing" />}
          {RANGES.map((range) => (
            <button
              key={range}
              type="button"
              aria-pressed={days === range}
              onClick={() => setDays(range)}
              className={`rounded-lg border px-3 py-1.5 text-xs font-semibold ${
                days === range
                  ? 'border-primary bg-primary text-white'
                  : 'border-gray-200 text-gray-600 hover:border-primary dark:border-gray-700 dark:text-gray-300'
              }`}
            >
              {range} days
            </button>
          ))}
        </div>
      </div>

      {isLoading && <RefreshCw className="animate-spin text-primary" />}
      {isError && <p className="text-sm text-red-500">Stats are unavailable right now.</p>}

      {data && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="New mail" value={data.mail.total} detail={`${data.mail.totals.important} important · ${data.mail.totals.spam} spam`} />
            <StatTile
              label="Trigger runs"
              value={data.triggers.total}
              detail={`${percent(data.triggers.success_rate)} succeeded · ${data.triggers.totals.failed} failed`}
            />
            <StatTile label="Memory facts" value={data.memory.active_facts} detail={`${data.memory.new_facts} new in range`} />
            <StatTile
              label="Messages"
              value={sum(data.messaging.totals)}
              detail={`${data.messaging.totals.whatsapp_in + data.messaging.totals.whatsapp_out} WhatsApp · ${data.messaging.totals.chat_user + data.messaging.totals.chat_assistant} chat`}
            />
          </div>

          <Card>
            <CardHeader><h3 className="font-bold">Mail</h3></CardHeader>
            <CardContent>
              <DailyBarChart
                title="New mail per day"
                data={data.mail.per_day}
                series={MAIL_SERIES}
                summary={`${data.mail.total} emails in ${days} days: ${data.mail.totals.important} important, ${data.mail.totals.digest} digest, ${data.mail.totals.ignore} ignored, ${data.mail.totals.spam} spam.`}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader><h3 className="font-bold">Triggers</h3></CardHeader>
            <CardContent className="space-y-4">
              <DailyBarChart
                title="Trigger runs per day"
                data={data.triggers.per_day}
                series={TRIGGER_SERIES}
                summary={`${data.triggers.total} runs in ${days} days, ${percent(data.triggers.success_rate)} of finished runs succeeded.`}
              />
              {data.triggers.definitions.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs" data-testid="trigger-definition-stats">
                    <thead>
                      <tr className="text-left text-gray-500 dark:text-gray-400">
                        <th className="py-1 pr-3 font-semibold">Definition</th>
                        <th className="py-1 pr-3 text-right font-semibold">Runs</th>
                        <th className="py-1 pr-3 text-right font-semibold">Succeeded</th>
                        <th className="py-1 pr-3 text-right font-semibold">Failed</th>
                        <th className="py-1 pr-3 text-right font-semibold">Avg. duration</th>
                        <th className="py-1 font-semibold">Last run</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.triggers.definitions.map((row) => (
                        <tr key={row.definition_id ?? 'ad-hoc'} className="border-t border-gray-100 dark:border-gray-800">
                          <td className="py-1.5 pr-3">
                            <span className="mr-2">{row.name ?? 'Ad-hoc runs'}</span>
                            {row.origin === 'agent' && <Badge variant="warning">By Janus</Badge>}
                          </td>
                          <td className="py-1.5 pr-3 text-right tabular-nums">{row.runs}</td>
                          <td className="py-1.5 pr-3 text-right tabular-nums">{percent(row.success_rate)}</td>
                          <td className="py-1.5 pr-3 text-right tabular-nums">{row.failed}</td>
                          <td className="py-1.5 pr-3 text-right tabular-nums">{formatDuration(row.avg_duration_seconds)}</td>
                          <td className="py-1.5">{formatServerDateTime(row.last_run_at) ?? row.last_run_at}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>

          <ApprovalStatsCard approvals={data.approvals} />

          <LlmUsageCard usage={data.llm} days={days} />

          <Card>
            <CardHeader><h3 className="font-bold">Memory &amp; knowledge</h3></CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <StatTile label="Active facts" value={data.memory.active_facts} detail={`${data.memory.archived_facts} archived`} />
                <StatTile label="Tracked topics" value={data.memory.active_topics} detail={`${data.memory.total_topics} in total`} />
                <StatTile label="Wiki pages" value={data.memory.wiki_pages} />
                <StatTile label="Knowledge sources" value={data.memory.knowledge_sources} />
              </div>
              <DailyBarChart
                title="New memory facts per day"
                data={data.memory.per_day}
                series={MEMORY_SERIES}
                summary={`${data.memory.new_facts} memory facts added in ${days} days.`}
                height={120}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader><h3 className="font-bold">Messaging</h3></CardHeader>
            <CardContent>
              <DailyBarChart
                title="Messages per day"
                data={data.messaging.per_day}
                series={MESSAGING_SERIES}
                summary={`${sum(data.messaging.totals)} messages in ${days} days.`}
              />
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
};

export default StatsPage;
