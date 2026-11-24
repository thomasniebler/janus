import type { Decorator } from '@storybook/react';

import { ChatSessionProvider } from '../contexts/ChatSessionContext';

/**
 * Wraps a story in a `ChatSessionProvider` (JAN-248), which chat views such as
 * `BriefingChat` and the shells that render them require. List it *before*
 * `withFakeWebSocket()` and `withQueryData(...)` in a `decorators` array so it
 * ends up inside them (later decorators wrap earlier ones).
 *
 * Kept apart from `storybookQueryClient.tsx` so vitest files importing that
 * helper (for `FakeWebSocket`) do not also pull in the api-backed provider.
 */
export function withChatSession(userId = 1): Decorator {
  return (Story) => (
    <ChatSessionProvider userId={userId}>
      <Story />
    </ChatSessionProvider>
  );
}
