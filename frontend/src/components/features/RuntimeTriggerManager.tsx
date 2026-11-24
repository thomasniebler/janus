import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Activity, CircleHelp } from 'lucide-react';
import { Link } from 'react-router-dom';
import { runtimeTriggerApi } from '../../api';
import type { RuntimeTriggerWorkerStatus } from '../../types';
import { CollapsibleCard } from '../ui/CollapsibleCard';
import { SuggestedAutomations } from './triggers/SuggestedAutomations';
import { TriggerDefinitionManager } from './triggers/TriggerDefinitionManager';
import { TriggerRunHistory } from './triggers/TriggerRunHistory';

const runtimeTriggerWorkerQueryKey = ['runtime-trigger-worker'] as const;

/** Shows the backend's real worker state so queued triggers are never silently stuck. */
const WorkerStatusBanner: React.FC<{ status?: RuntimeTriggerWorkerStatus; isError: boolean }> = ({ status, isError }) => {
  if (isError) {
    return (
      <div role="status" data-worker-state="unknown" className="rounded-xl border border-gray-300 bg-gray-50 px-4 py-3 text-sm text-gray-600 dark:border-gray-700 dark:bg-gray-900/40 dark:text-gray-300">
        Could not load the trigger worker status.
      </div>
    );
  }
  if (!status) return null;
  if (status.running) {
    return (
      <div role="status" data-worker-state="running" className="rounded-xl border border-green-800 bg-green-900/20 px-4 py-3 text-sm text-green-300">
        Trigger worker is running. Queued triggers are executed automatically.
      </div>
    );
  }
  if (status.enabled) {
    return (
      <div role="status" data-worker-state="stopped" className="rounded-xl border border-amber-700 bg-amber-900/20 px-4 py-3 text-sm text-amber-300">
        Trigger worker is enabled but not running in this server process, so new triggers will stay queued. Check the server logs.
      </div>
    );
  }
  return (
    <div role="status" data-worker-state="disabled" className="rounded-xl border border-amber-700 bg-amber-900/20 px-4 py-3 text-sm text-amber-300">
      Trigger worker is disabled by configuration, so triggers stay queued and never run. Set <code>runtime_trigger_worker_enabled = true</code> (or <code>JANUS_RUNTIME_TRIGGER_WORKER_ENABLED=true</code>) and restart Janus to execute them.
    </div>
  );
};

export const RuntimeTriggerManager: React.FC = () => {
  const [highlightedDefinitionId, setHighlightedDefinitionId] = useState<number | null>(null);
  const { data: workerStatus, isError: isWorkerStatusError } = useQuery({
    queryKey: runtimeTriggerWorkerQueryKey,
    queryFn: async () => (await runtimeTriggerApi.workerStatus()).data,
    refetchInterval: 15000,
  });
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold flex items-center gap-2"><Activity size={22} className="text-primary" /> Triggers</h2>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Everything Janus does outside chat — morning briefing, afternoon digest, mail processing, and your own automations — is a trigger. Each run speaks as your Janus character.</p>
      </div>

      <WorkerStatusBanner status={workerStatus} isError={isWorkerStatusError} />

      <CollapsibleCard title="Help and examples" icon={<CircleHelp size={20} />} defaultCollapsed>
        <div className="space-y-4 p-5 text-sm text-gray-600 dark:text-gray-300">
          <p>
            A trigger definition is an objective (what Janus should do), the tools it may use, and when it runs:
            on demand, on a recurring schedule, or when an event such as a new email arrives. Every activation becomes one
            run with its own result below.
          </p>
          <div className="grid gap-3 md:grid-cols-3">
            <div className="rounded-xl border border-gray-200 p-3 dark:border-gray-700">
              <h3 className="font-semibold text-gray-900 dark:text-gray-100">Manual</h3>
              <p className="mt-1 text-xs">Runs only when you press <em>Run now</em>. Useful for one-off chores like drafting a status email.</p>
            </div>
            <div className="rounded-xl border border-gray-200 p-3 dark:border-gray-700">
              <h3 className="font-semibold text-gray-900 dark:text-gray-100">Recurring schedule</h3>
              <p className="mt-1 text-xs">Daily at a time (optionally on selected weekdays) or every N minutes. A missed occurrence runs once when Janus is back.</p>
            </div>
            <div className="rounded-xl border border-gray-200 p-3 dark:border-gray-700">
              <h3 className="font-semibold text-gray-900 dark:text-gray-100">Event</h3>
              <p className="mt-1 text-xs">Runs once per event, e.g. <code>mail.received</code> for every new email that no SPAM/IGNORE rule filtered out, a new WhatsApp message, or a changed planner task.</p>
            </div>
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Nobody is watching a trigger run, so tools that are off for unattended runs (Settings → Tools &amp; Connections)
            are refused, and every refusal is written into the run's result. Picking a tool on a trigger you created yourself allows it for that trigger.
          </p>
          <Link to="/docs/runtime-triggers" className="inline-flex font-medium text-primary hover:underline">
            Read the runtime trigger documentation
          </Link>
        </div>
      </CollapsibleCard>

      <SuggestedAutomations />

      <TriggerDefinitionManager highlightedId={highlightedDefinitionId} />

      <TriggerRunHistory onSelectDefinition={setHighlightedDefinitionId} />
    </div>
  );
};
