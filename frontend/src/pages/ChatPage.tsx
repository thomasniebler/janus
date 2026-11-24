import React from 'react';

import { BriefingChat } from '../components/features/BriefingChat';
import { TurnTimeline } from '../components/features/TurnTimeline';
import { TurnTimelineDockContext } from '../components/features/turnTimelineDock';
import { ErrorBoundary } from '../components/ui/ErrorBoundary';
import { useConversationDeepLink } from '../hooks/useConversationDeepLink';
import { useMediaQuery } from '../hooks/useMediaQuery';

// Viewport height minus each shell's chrome (measured in a browser): UIv1's 56px
// header, UIv2's/UIv3's taller top nav (plus its compact nav row below `md`), the
// `<main>` padding and the footer. `dvh` keeps mobile browser toolbars from
// pushing the composer off screen; the floor keeps the panel usable on very short
// viewports (the page then scrolls as a whole, but still never overlaps the
// footer). Update these offsets when a shell's header, padding or footer changes.
const CHAT_PAGE_HEIGHT_CLASS = [
  'flex min-h-[32rem] flex-col gap-4',
  'h-[calc(100dvh-10rem)]',
  'uiv2:h-[calc(100dvh-16.25rem)] md:uiv2:h-[calc(100dvh-12.75rem)]',
  'uiv3:h-[calc(100dvh-15.5rem)] md:uiv3:h-[calc(100dvh-13.25rem)]',
].join(' ');

/** Tailwind's `xl` breakpoint: wide enough to dock the turn timeline beside the transcript. */
const TIMELINE_COLUMN_MEDIA_QUERY = '(min-width: 1280px)';

/**
 * The full-width chat surface (JAN-248): conversation list plus transcript, a
 * view of the same tab-wide session the shell sidebar shows elsewhere. The
 * shells render no sidebar/inline chat on this route, so the session is never
 * shown twice.
 *
 * The page is pinned to the viewport (JAN-273): its height is the viewport minus
 * the active shell's chrome (header, `<main>` padding and footer), so it never
 * pushes the footer down or paints over it. Only the transcript inside
 * `BriefingChat` scrolls; the grid row is `minmax(0,1fr)` so the panel is bounded
 * by the page instead of growing to fit its content.
 *
 * The turn timeline (JAN-316) docks as a second column at `xl` and above;
 * narrower, `BriefingChat` shows its collapsed copy above the conversation.
 * Exactly one copy is mounted (picked with `useMediaQuery`, like the shells'
 * chat placement).
 */
const ChatPage: React.FC = () => {
  const timelineColumn = useMediaQuery(TIMELINE_COLUMN_MEDIA_QUERY);
  useConversationDeepLink();
  return (
    <div className={CHAT_PAGE_HEIGHT_CLASS} data-testid="chat-page">
      <h1 className="sr-only">Chat</h1>
      <div
        className={`grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)] gap-4 ${
          timelineColumn ? 'grid-cols-[minmax(0,1fr)_20rem]' : 'grid-cols-1'
        }`}
      >
        <section aria-label="Conversation" className="flex min-h-0 flex-col">
          <ErrorBoundary title="The chat crashed">
            <TurnTimelineDockContext.Provider value={timelineColumn}>
              <BriefingChat variant="fullPage" />
            </TurnTimelineDockContext.Provider>
          </ErrorBoundary>
        </section>
        {timelineColumn && (
          <aside aria-label="Plan steps" className="min-h-0 overflow-y-auto">
            {/* Tool details and approvals stay inline under each reply (`ToolCallInspector`, JAN-249/JAN-254). */}
            <ErrorBoundary title="The turn timeline crashed">
              <TurnTimeline />
            </ErrorBoundary>
          </aside>
        )}
      </div>
    </div>
  );
};

export default ChatPage;
