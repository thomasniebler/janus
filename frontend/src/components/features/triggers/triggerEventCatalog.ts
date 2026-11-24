import type { TriggerEventSpec, TriggerSubsystemSpec } from '../../../types';

/** Query key of `GET /api/runtime/definitions/event-catalog` (JAN-210). */
export const triggerEventCatalogQueryKey = ['trigger-event-catalog'] as const;

/** Where `eventType` lives in the catalog, or null for a value the backend does not publish. */
export const findCatalogEvent = (
  catalog: TriggerSubsystemSpec[],
  eventType: string | null | undefined,
): { subsystem: TriggerSubsystemSpec; event: TriggerEventSpec } | null => {
  for (const subsystem of catalog) {
    const event = subsystem.events.find((candidate) => candidate.event_type === eventType);
    if (event) return { subsystem, event };
  }
  return null;
};

/** The catalog's first event, used when an editor switches to event activation. */
export const firstCatalogEvent = (catalog: TriggerSubsystemSpec[]): string | null =>
  catalog.find((subsystem) => subsystem.events.length > 0)?.events[0]?.event_type ?? null;

/** "Mail · New email received", falling back to the raw value for anything uncatalogued. */
export const describeCatalogEvent = (catalog: TriggerSubsystemSpec[], eventType: string | null | undefined): string => {
  const found = findCatalogEvent(catalog, eventType);
  return found ? `${found.subsystem.label} · ${found.event.label}` : eventType ?? '?';
};
