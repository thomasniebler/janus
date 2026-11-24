import React from 'react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

import type { RuntimeTrigger } from '../../../types';
import { TriggerWidget, type TriggerWidgetStyle } from './TriggerWidget';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export interface TriggerWidgetListProps {
  triggers: RuntimeTrigger[];
  widgetStyle?: TriggerWidgetStyle;
  /** Makes each run's definition name a link (JAN-256). */
  onSelectDefinition?: (definitionId: number) => void;
  /** Extra links under each run (card and timeline styles), e.g. to its plan steps (JAN-204). */
  renderActions?: (trigger: RuntimeTrigger) => React.ReactNode;
  className?: string;
}

/**
 * Layout wrapper that gives each widget style the container it was designed for.
 *
 * The row style needs a single hairline-ruled frame so consecutive rows read as
 * one ledger; the card and timeline styles are free-standing and only need
 * spacing between them.
 */
export const TriggerWidgetList: React.FC<TriggerWidgetListProps> = ({
  triggers,
  widgetStyle = 'card',
  onSelectDefinition,
  renderActions,
  className,
}) => {
  if (widgetStyle === 'row') {
    return (
      <div
        className={cn(
          'overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700 uiv2:rounded-none uiv2:border-v2-border uiv3:rounded-none uiv3:border-2 uiv3:border-v3-border',
          className
        )}
      >
        {triggers.map((trigger) => (
          <TriggerWidget key={trigger.trigger_id} trigger={trigger} widgetStyle="row" onSelectDefinition={onSelectDefinition} />
        ))}
      </div>
    );
  }

  return (
    <div className={cn(widgetStyle === 'timeline' ? 'space-y-4' : 'space-y-3', className)}>
      {triggers.map((trigger) => (
        <TriggerWidget
          key={trigger.trigger_id}
          trigger={trigger}
          widgetStyle={widgetStyle}
          onSelectDefinition={onSelectDefinition}
          actions={renderActions?.(trigger)}
        />
      ))}
    </div>
  );
};
