import React from 'react';
import type { TriggerSubsystemSpec } from '../../../types';
import { findCatalogEvent } from './triggerEventCatalog';

const selectClass =
  'block w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-gray-700 rounded-xl outline-none text-sm';
const labelClass = 'text-xs font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider ml-1';

interface TriggerEventPickerProps {
  catalog: TriggerSubsystemSpec[];
  value: string | null | undefined;
  onChange: (eventType: string) => void;
}

/**
 * Dependent subsystem -> event dropdowns over the backend's event catalog (JAN-210).
 *
 * Only catalogued values can be picked, so a saved definition always names an
 * event the backend actually publishes. A stored value outside the catalog is
 * shown as unavailable until the user picks a real one.
 */
export const TriggerEventPicker: React.FC<TriggerEventPickerProps> = ({ catalog, value, onChange }) => {
  const found = findCatalogEvent(catalog, value);
  const [chosenSubsystem, setChosenSubsystem] = React.useState<string | null>(null);
  const subsystemId = found?.subsystem.subsystem ?? chosenSubsystem ?? '';
  const subsystem = catalog.find((candidate) => candidate.subsystem === subsystemId);
  const unknown = Boolean(value) && !found;

  const chooseSubsystem = (next: string) => {
    setChosenSubsystem(next);
    const first = catalog.find((candidate) => candidate.subsystem === next)?.events[0];
    if (first) onChange(first.event_type);
  };

  if (catalog.length === 0) {
    return <p className="text-sm text-gray-500 dark:text-gray-400 md:col-span-2">Loading the available events…</p>;
  }

  return (
    <>
      <div className="space-y-1.5">
        <label className={labelClass} htmlFor="trigger-definition-subsystem">Subsystem</label>
        <select
          id="trigger-definition-subsystem"
          value={subsystemId}
          required
          onChange={(event) => chooseSubsystem(event.target.value)}
          className={selectClass}
        >
          <option value="" disabled>Choose a subsystem</option>
          {catalog.map((candidate) => (
            <option key={candidate.subsystem} value={candidate.subsystem} title={candidate.description}>
              {candidate.label}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-1.5">
        <label className={labelClass} htmlFor="trigger-definition-event">Event</label>
        <select
          id="trigger-definition-event"
          value={found ? found.event.event_type : ''}
          required
          disabled={!subsystem}
          onChange={(event) => onChange(event.target.value)}
          className={selectClass}
        >
          <option value="" disabled>Choose an event</option>
          {(subsystem?.events ?? []).map((event) => (
            <option key={event.event_type} value={event.event_type} title={event.description}>
              {event.label}
            </option>
          ))}
        </select>
      </div>
      <div className="md:col-span-2 space-y-1 text-xs text-gray-500 dark:text-gray-400" data-testid="trigger-event-details">
        {unknown && (
          <p className="text-amber-600 dark:text-amber-400">
            “{value}” is not an event Janus publishes, so this trigger never fires. Choose one of the events above.
          </p>
        )}
        {found && (
          <>
            <p>{found.event.description}</p>
            <p>
              Each run receives:{' '}
              {found.event.payload.map((field, index) => (
                <React.Fragment key={field.name}>
                  {index > 0 && ', '}
                  <code title={field.description}>{field.name}</code>
                </React.Fragment>
              ))}
            </p>
          </>
        )}
      </div>
    </>
  );
};
