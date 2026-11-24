import { useEffect } from 'react';

import ChatPage from '../pages/ChatPage';
import { FakeWebSocket } from './storybookQueryClient';

const REPLY =
  'Two meetings today and nothing overdue. The Q3 roadmap review moved to 14:00, ' +
  'and the hiring pipeline has three candidates waiting for feedback.';

/**
 * The `/chat` page with a transcript far taller than the viewport (JAN-273), for
 * the shells' chat-page stories: only the conversation history should scroll,
 * and the footer must stay below the page. Streams the replies over the story's
 * `FakeWebSocket`, so wrap it in `withChatSession()` and `withFakeWebSocket()`.
 */
export const LongTranscriptChatPage = ({ replies = 30 }: { replies?: number }) => {
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
      for (let turn = 1; turn <= replies; turn += 1) {
        socket?.emit('__START__');
        socket?.emit(`**Update ${turn}.** ${REPLY}`);
        socket?.emit('__END__');
      }
    }, 10);
    return () => window.clearTimeout(timer);
  }, [replies]);
  return <ChatPage />;
};
