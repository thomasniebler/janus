import React from 'react';

import type { RuntimeTriggerStatus } from '../../../types';
import { STATUS_PRESENTATION } from '../triggers/triggerPresentation';

/** A task's lifecycle status, in the trigger widgets' shared vocabulary (all three shells). */
export const TaskStatusBadge: React.FC<{ status: RuntimeTriggerStatus }> = ({ status }) => {
  const presentation = STATUS_PRESENTATION[status] ?? STATUS_PRESENTATION.queued;
  const Icon = presentation.icon;
  return (
    <span
      data-status={status}
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${presentation.badgeClass}`}
    >
      <Icon size={12} aria-hidden className={presentation.inFlight ? 'animate-spin' : undefined} />
      {presentation.label}
    </span>
  );
};
