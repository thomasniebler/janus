import React from 'react';

import type { StatsSummary } from '../../../types';
import { Card, CardContent, CardHeader } from '../../ui/Card';
import { DailyBarChart, type DailySeries } from './DailyBarChart';
import { StatTile } from './StatTile';

type Usage = StatsSummary['llm'];

const SERIES: DailySeries<'input_tokens' | 'output_tokens'>[] = [
  { key: 'input_tokens', label: 'Input tokens', color: 'var(--viz-1)' },
  { key: 'output_tokens', label: 'Output tokens', color: 'var(--viz-2)' },
];

const SURFACE_LABELS: Record<string, string> = {
  chat: 'Chat',
  trigger: 'Trigger runs',
  delegated: 'Delegated tasks',
  other: 'Other (mail, extraction, ...)',
};

const tokens = (value: number): string =>
  value >= 1_000_000 ? `${(value / 1_000_000).toFixed(1)}M` : value >= 10_000 ? `${Math.round(value / 1000)}k` : String(value);

/** How many model calls and tokens Janus used, by surface and model (JAN-408). */
export const LlmUsageCard: React.FC<{ usage: Usage; days: number }> = ({ usage, days }) => (
  <Card>
    <CardHeader>
      <h3 className="font-bold">LLM usage</h3>
    </CardHeader>
    <CardContent className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Model calls" value={usage.totals.calls} />
        <StatTile label="Input tokens" value={tokens(usage.totals.input_tokens)} />
        <StatTile label="Output tokens" value={tokens(usage.totals.output_tokens)} />
        <StatTile
          label="Calls without token counts"
          value={usage.totals.unreported_calls}
          detail="Some streamed answers report no usage."
        />
      </div>
      <DailyBarChart
        title="Tokens per day"
        data={usage.per_day}
        series={SERIES}
        summary={`${tokens(usage.totals.input_tokens + usage.totals.output_tokens)} tokens in ${days} days (days follow UTC).`}
        height={120}
      />
      {usage.by_surface.length > 0 && (
        <div className="grid gap-3 md:grid-cols-2">
          <table className="w-full text-sm" aria-label="Usage by surface">
            <thead><tr className="text-left text-gray-400"><th>Surface</th><th className="text-right">Calls</th><th className="text-right">Tokens</th></tr></thead>
            <tbody>
              {usage.by_surface.map((row) => (
                <tr key={row.surface}><td>{SURFACE_LABELS[row.surface] ?? row.surface}</td><td className="text-right">{row.calls}</td><td className="text-right">{tokens(row.input_tokens + row.output_tokens)}</td></tr>
              ))}
            </tbody>
          </table>
          <table className="w-full text-sm" aria-label="Usage by model">
            <thead><tr className="text-left text-gray-400"><th>Model</th><th className="text-right">Calls</th><th className="text-right">Tokens</th></tr></thead>
            <tbody>
              {usage.by_model.map((row) => (
                <tr key={row.model}><td className="break-all">{row.model}</td><td className="text-right">{row.calls}</td><td className="text-right">{tokens(row.input_tokens + row.output_tokens)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </CardContent>
  </Card>
);
