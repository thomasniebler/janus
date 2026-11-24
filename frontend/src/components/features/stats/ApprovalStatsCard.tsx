import React from 'react';

import type { StatsSummary } from '../../../types';
import { Card, CardContent, CardHeader } from '../../ui/Card';
import { percent } from './format';
import { StatTile } from './StatTile';

const formatHours = (hours: number | null): string => {
  if (hours === null) return '';
  if (hours < 1) return ` · median ${Math.round(hours * 60)} min to decide`;
  return ` · median ${hours < 10 ? hours.toFixed(1) : Math.round(hours)} h to decide`;
};

/** How often the user approves what Janus proposes (JAN-371). */
export const ApprovalStatsCard: React.FC<{ approvals: StatsSummary['approvals'] }> = ({ approvals }) => (
  <Card>
    <CardHeader><h3 className="font-bold">Approvals</h3></CardHeader>
    <CardContent className="space-y-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatTile
          label="Suggested automations"
          value={percent(approvals.automations.approval_rate)}
          detail={`${approvals.automations.totals.approved} approved · ${approvals.automations.totals.rejected} rejected · ${approvals.automations.totals.proposed} open${formatHours(approvals.automations.median_hours_to_decision)}`}
        />
        <StatTile
          label="Tool approvals in chat"
          value={percent(approvals.chat_tools.approval_rate)}
          detail={`${approvals.chat_tools.totals.approved} allowed · ${approvals.chat_tools.totals.denied} denied · ${approvals.chat_tools.totals.timeout} timed out`}
        />
        <StatTile
          label="Character changes"
          value={percent(approvals.character.approval_rate)}
          detail={`${approvals.character.totals.approved} approved · ${approvals.character.totals.rejected} rejected · ${approvals.character.totals.proposed} open${formatHours(approvals.character.median_hours_to_decision)}`}
        />
      </div>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Share of decided proposals you approved, counted by the day Janus proposed them. Timeouts and withdrawn
        suggestions are not decisions. A rate near 100% can also mean approvals are not being read closely.
      </p>
    </CardContent>
  </Card>
);
