import { formatServerTime } from '../../lib/serverTime';
import { Trash2 } from 'lucide-react';
import { Badge } from '../ui/Badge';
import { calendarEventItemId } from '../../hooks/pageContextSections';
import type { CalendarEvent } from '../../types';

interface DashboardScheduleListProps {
  /** Today's events from `/api/dashboard/events`: local events plus read-only imported ones. */
  events: CalendarEvent[];
  /** Asks to delete a local event (JAN-221); imported events are read-only and never offer it. */
  onDelete?: (event: CalendarEvent) => void;
}

/** The dashboard "Schedule" timeline: one row per event, time first. */
export function DashboardScheduleList({ events, onDelete }: DashboardScheduleListProps) {
  return (
    <>
      {events.map((event) => (
        <div
          key={`${event.source}-${event.id}`}
          data-ui-item={`calendar_event:${calendarEventItemId(event)}`}
          className="flex gap-4 group"
        >
          <div className="flex flex-col items-center min-w-[50px]">
            <span className="text-xs font-bold text-primary uppercase">
              {formatServerTime(event.start_time, {
                hour: '2-digit',
                minute: '2-digit',
                hour12: false,
              })}
            </span>
            <div className="w-0.5 h-full bg-gray-100 dark:bg-gray-800 my-1 group-last:hidden"></div>
          </div>
          <div className="min-w-0 flex-1 pb-4 group-last:pb-0">
            <div className="flex items-center gap-2">
              <h3 className="font-bold text-sm">{event.title}</h3>
              <Badge variant={event.read_only ? 'secondary' : 'primary'}>{event.source_label}</Badge>
              {!event.read_only && onDelete ? (
                <button
                  type="button"
                  aria-label={`Delete ${event.title}`}
                  title="Delete event"
                  onClick={() => onDelete(event)}
                  className="ml-auto shrink-0 rounded p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-accent dark:hover:bg-gray-800"
                >
                  <Trash2 size={14} />
                </button>
              ) : null}
            </div>
            {event.location && <p className="text-xs text-gray-400 mt-0.5">{event.location}</p>}
          </div>
        </div>
      ))}
    </>
  );
}
