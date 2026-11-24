import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { ChatSessionContext, type ChatSessionValue } from '../contexts/chatSession';
import { CONVERSATION_PARAM, useConversationDeepLink } from './useConversationDeepLink';

const setup = (url: string, isRestoring = false) => {
  const selectConversation = vi.fn().mockResolvedValue(undefined);
  const session = { selectConversation, isRestoring } as unknown as ChatSessionValue;
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[url]}>
      <ChatSessionContext.Provider value={session}>{children}</ChatSessionContext.Provider>
    </MemoryRouter>
  );
  const view = renderHook(
    () => {
      useConversationDeepLink();
      return useLocation();
    },
    { wrapper }
  );
  return { selectConversation, view };
};

describe('useConversationDeepLink (JAN-204)', () => {
  it('opens the linked conversation and drops the parameter', async () => {
    const { selectConversation, view } = setup(`/chat?${CONVERSATION_PARAM}=12&ui=v2`);
    await waitFor(() => expect(view.result.current.search).toBe('?ui=v2'));
    expect(selectConversation).toHaveBeenCalledWith(12);
  });

  it('ignores an invalid id', async () => {
    const { selectConversation, view } = setup('/chat?conversation=abc');
    await waitFor(() => expect(view.result.current.search).toBe(''));
    expect(selectConversation).not.toHaveBeenCalled();
  });

  it('waits until the last conversation has been restored', () => {
    const { selectConversation, view } = setup('/chat?conversation=5', true);
    expect(selectConversation).not.toHaveBeenCalled();
    expect(view.result.current.search).toBe('?conversation=5');
  });

  it('does nothing without the parameter', () => {
    const { selectConversation } = setup('/chat');
    expect(selectConversation).not.toHaveBeenCalled();
  });
});
