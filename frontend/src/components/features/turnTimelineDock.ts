import { createContext } from 'react';

/**
 * True inside a view that docks the turn timeline itself (`/chat`, JAN-316), so
 * `BriefingChat` leaves out its collapsed copy and the timeline is shown once.
 */
export const TurnTimelineDockContext = createContext(false);
