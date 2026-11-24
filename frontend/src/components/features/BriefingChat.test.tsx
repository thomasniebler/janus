import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { act, StrictMode, useState } from 'react';
import type React from 'react';
import { Link, MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { characterApi, conversationApi } from '../../api';
import { delegatedTasksApi } from '../../lib/delegatedTasks';
import { ChatSessionProvider } from '../../contexts/ChatSessionContext';
import { activeConversationStorageKey } from '../../contexts/chatSession';
import { PageContextProvider } from '../../contexts/PageContextProvider';
import { UIActionProvider } from '../../contexts/UIActionProvider';
import { usePageContext } from '../../hooks/usePageContext';
import ChatPage from '../../pages/ChatPage';
import { FakeWebSocket } from '../../test/storybookQueryClient';
import type { User } from '../../types';
import { isChatRoute } from '../layout/useNavItems';
import { BriefingChat } from './BriefingChat';

// BriefingChat is a view over ChatSessionProvider (JAN-248), so both are tested here:
// with `isolate: false`, a second test file importing BriefingChat would share its
// module (and the api mock it bound to) with this one.
vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});
// The delegated-tasks panel (JAN-208) reads through its own module: spy on the real
// object (see DelegatedTasksPanel.test.tsx for why not `vi.mock`), empty by default.
beforeEach(() => {
  vi.spyOn(delegatedTasksApi, 'list').mockResolvedValue({
    data: { items: [], total: 0, limit: 10, offset: 0, has_more: false },
  } as never);
});
afterEach(() => {
  vi.mocked(delegatedTasksApi.list).mockRestore();
});

const currentUser: User = { id: 1, username: 'janus-admin', is_admin: true, google_linked: false };
const STORAGE_KEY = activeConversationStorageKey(currentUser.id);

const newQueryClient = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });

const renderChat = () =>
  render(
    <QueryClientProvider client={newQueryClient()}>
      <ChatSessionProvider userId={currentUser.id}>
        <BriefingChat />
      </ChatSessionProvider>
    </QueryClientProvider>
  );

type View = 'sidebar' | 'page' | 'none';

/**
 * A stand-in app shell with the same chat contract as the real ones (sidebar chat
 * everywhere except /chat). The real shells' single-mount placement is covered by
 * their own test files; importing them here as well would share their modules
 * across test files (`isolate: false`) with a different BriefingChat mock.
 */
const StandInShell = ({ children }: { children: React.ReactNode }) => {
  const { pathname } = useLocation();
  return (
    <div>
      <nav aria-label="Primary">
        <Link to="/">Dashboard</Link>
        <Link to="/emails">Email Preview</Link>
        <Link to="/chat">Chat</Link>
      </nav>
      <main>{children}</main>
      {!isChatRoute(pathname) && (
        <aside aria-label="Chat with Janus">
          <BriefingChat variant="compactSidebar" />
        </aside>
      )}
    </div>
  );
};

const ChatView = ({ view }: { view: View }) => {
  if (view === 'none') return <p>No chat view mounted</p>;
  return <BriefingChat variant={view === 'sidebar' ? 'compactSidebar' : 'fullPage'} />;
};

const renderSession = (view: View, queryClient = newQueryClient()) => {
  const tree = (nextView: View) => (
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <ChatSessionProvider userId={currentUser.id}>
          <ChatView view={nextView} />
        </ChatSessionProvider>
      </QueryClientProvider>
    </StrictMode>
  );
  const result = render(tree(view));
  return { ...result, showView: (nextView: View) => result.rerender(tree(nextView)) };
};

/** Lets the provider's deferred (setTimeout 0) socket connect run. */
const flushTimers = () => act(() => new Promise((resolve) => window.setTimeout(resolve, 20)));

const latestSocket = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1];

const streamReply = (socket: FakeWebSocket, text: string) => {
  act(() => {
    socket.emit('__START__');
    socket.emit(text);
    socket.emit('__END__');
  });
};

describe('ChatSessionProvider', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket);
    vi.mocked(conversationApi.list).mockResolvedValue({ data: [] } as never);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('opens exactly one /ws/chat socket across StrictMode, re-renders and view remounts', async () => {
    const { showView } = renderSession('sidebar');
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));

    showView('page');
    showView('none');
    showView('sidebar');
    showView('sidebar');
    await flushTimers();

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.instances[0].url).toContain('/ws/chat');
    expect(FakeWebSocket.instances[0].readyState).toBe(FakeWebSocket.OPEN);
  });

  it('drops the previous account\'s socket, transcript and draft when the signed-in user changes', async () => {
    const user = userEvent.setup();
    const otherUser: User = { id: 2, username: 'someone-else', is_admin: false, google_linked: false };
    const client = newQueryClient();
    const tree = (userId: number) => (
      <QueryClientProvider client={client}>
        <ChatSessionProvider userId={userId}>
          <BriefingChat />
        </ChatSessionProvider>
      </QueryClientProvider>
    );

    const { rerender } = render(tree(currentUser.id));
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const firstSocket = FakeWebSocket.instances[0];
    streamReply(firstSocket, 'Secret for user one.');
    await user.type(screen.getByPlaceholderText('Ask a question...'), 'Unsent draft');
    expect(screen.getByText('Secret for user one.')).toBeInTheDocument();

    rerender(tree(otherUser.id));

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
    expect(firstSocket.readyState).toBe(FakeWebSocket.CLOSED);
    expect(screen.queryByText('Secret for user one.')).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText('Ask a question...')).toHaveValue('');
  });

  it('keeps the transcript and draft when a view unmounts and a different one mounts', async () => {
    const user = userEvent.setup();
    const { showView } = renderSession('sidebar');
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));

    streamReply(latestSocket(), 'Two meetings today.');
    await user.type(screen.getByPlaceholderText('Ask a question...'), 'What about tomorrow?');

    showView('none');
    expect(screen.queryByText('Two meetings today.')).not.toBeInTheDocument();

    showView('page');
    expect(screen.getByText('Two meetings today.')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Ask a question...')).toHaveValue('What about tomorrow?');
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it('sends the draft with the current page context over the shared socket and clears it', async () => {
    const user = userEvent.setup();
    renderSession('sidebar');
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));

    await user.type(screen.getByPlaceholderText('Ask a question...'), 'Summarise my inbox{Enter}');

    expect(latestSocket().sent.map((frame) => JSON.parse(frame))).toEqual([
      { type: 'message', text: 'Summarise my inbox', page_context: { page: 'Dashboard', route: '/' } },
    ]);
    expect(screen.getByText('Summarise my inbox')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Ask a question...')).toHaveValue('');
  });

  it('sends the snapshot the current page published (JAN-250)', async () => {
    const user = userEvent.setup();
    const PublishingPage = () => {
      usePageContext({
        page: 'Emails',
        filters: { priority: 'important' },
        visible_items: [{ type: 'email', id: 7, label: 'Invoice — from billing@example.com' }],
      });
      return null;
    };
    render(
      <MemoryRouter initialEntries={['/']}>
        <QueryClientProvider client={newQueryClient()}>
          <PageContextProvider>
            <ChatSessionProvider userId={currentUser.id}>
              <PublishingPage />
              <BriefingChat />
            </ChatSessionProvider>
          </PageContextProvider>
        </QueryClientProvider>
      </MemoryRouter>
    );
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));

    await user.type(screen.getByPlaceholderText('Ask a question...'), 'what am I looking at?{Enter}');

    expect(JSON.parse(latestSocket().sent[0])).toEqual({
      type: 'message',
      text: 'what am I looking at?',
      page_context: {
        page: 'Emails',
        route: '/',
        filters: { priority: 'important' },
        visible_items: [{ type: 'email', id: 7, label: 'Invoice — from billing@example.com' }],
      },
    });
  });

  it('restores the active conversation and its messages after the provider remounts', async () => {
    const first = renderSession('sidebar');
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    act(() => latestSocket().emit('__CONVERSATION_ID__:7'));
    await waitFor(() => expect(window.sessionStorage.getItem(STORAGE_KEY)).toBe('7'));

    first.unmount();
    expect(FakeWebSocket.instances[0].readyState).toBe(FakeWebSocket.CLOSED);

    vi.mocked(conversationApi.get).mockResolvedValue({
      data: {
        id: 7,
        title: 'Planning',
        created_at: '2026-09-28T08:00:00Z',
        updated_at: '2026-09-28T08:05:00Z',
        messages: [
          { role: 'user', content: 'Earlier question' },
          { role: 'assistant', content: 'Earlier answer' },
        ],
      },
    } as never);

    renderSession('page');

    expect(await screen.findByText('Earlier answer')).toBeInTheDocument();
    expect(conversationApi.get).toHaveBeenCalledWith(7);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
    await flushTimers();
    // The remounted session opens one socket, bound to the restored conversation.
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(latestSocket().url).toContain('conversation_id=7');
  });

  it('starts a fresh chat when the stored conversation no longer loads', async () => {
    window.sessionStorage.setItem(STORAGE_KEY, '99');
    vi.mocked(conversationApi.get).mockRejectedValue(new Error('404'));

    renderSession('sidebar');

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    expect(latestSocket().url).not.toContain('conversation_id');
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('shows a conversation selected in one view as the active one in the other', async () => {
    const user = userEvent.setup();
    vi.mocked(conversationApi.list).mockResolvedValue({
      data: [{ id: 5, title: 'Hiring pipeline', created_at: '2026-09-27T10:00:00Z', updated_at: '2026-09-27T10:00:00Z' }],
    } as never);
    vi.mocked(conversationApi.get).mockResolvedValue({
      data: {
        id: 5,
        title: 'Hiring pipeline',
        created_at: '2026-09-27T10:00:00Z',
        updated_at: '2026-09-27T10:00:00Z',
        messages: [{ role: 'assistant', content: 'Three candidates are waiting.' }],
      },
    } as never);

    const { showView } = renderSession('page');
    await user.click(await screen.findByRole('button', { name: 'Hiring pipeline' }));
    expect(await screen.findByText('Three candidates are waiting.')).toBeInTheDocument();

    showView('sidebar');

    expect(screen.getByText('Three candidates are waiting.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hiring pipeline' })).toHaveAttribute('aria-current', 'true');
    await waitFor(() => expect(latestSocket().url).toContain('conversation_id=5'));
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBe('5');
  });

  it('keeps the session when navigating from the shell sidebar to /chat and back', async () => {
    const user = userEvent.setup();

    render(
      <StrictMode>
        <QueryClientProvider client={newQueryClient()}>
          <MemoryRouter initialEntries={['/']}>
            <ChatSessionProvider userId={currentUser.id}>
              <StandInShell>
                <Routes>
                  <Route path="/" element={<h1>Dashboard page</h1>} />
                  <Route path="/emails" element={<h1>Emails page</h1>} />
                  <Route path="/chat" element={<ChatPage />} />
                </Routes>
              </StandInShell>
            </ChatSessionProvider>
          </MemoryRouter>
        </QueryClientProvider>
      </StrictMode>
    );
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));

    const sidebar = screen.getByRole('complementary', { name: 'Chat with Janus' });
    streamReply(latestSocket(), 'Nothing overdue.');
    await user.type(within(sidebar).getByPlaceholderText('Ask a question...'), 'Draft in progress');

    await user.click(screen.getByRole('link', { name: 'Chat' }));

    // On /chat the page is the only chat view; the sidebar is gone.
    expect(screen.getByTestId('chat-page')).toBeInTheDocument();
    expect(screen.queryByRole('complementary', { name: 'Chat with Janus' })).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText('Ask a question...')).toHaveValue('Draft in progress');
    expect(screen.getByText('Nothing overdue.')).toBeInTheDocument();

    await user.click(screen.getByRole('link', { name: 'Email Preview' }));

    const sidebarAgain = screen.getByRole('complementary', { name: 'Chat with Janus' });
    expect(within(sidebarAgain).getByText('Nothing overdue.')).toBeInTheDocument();
    expect(within(sidebarAgain).getByPlaceholderText('Ask a question...')).toHaveValue('Draft in progress');
    await flushTimers();
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});

describe('BriefingChat', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket);
    vi.mocked(conversationApi.list).mockResolvedValue({ data: [] } as never);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('connects without an agent_id query parameter (JAN-163/JAN-167 cutover)', async () => {
    renderChat();

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));

    expect(FakeWebSocket.instances[0].url).not.toContain('agent_id');
  });

  it('deletes a chat only after the in-app confirmation (JAN-292)', async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, 'confirm');
    vi.mocked(conversationApi.list).mockResolvedValue({
      data: [{ id: 5, title: 'Hiring pipeline', created_at: '2026-09-27T10:00:00Z', updated_at: '2026-09-27T10:00:00Z' }],
    } as never);
    vi.mocked(conversationApi.delete).mockResolvedValue({ data: undefined } as never);
    renderChat();

    await user.click(await screen.findByRole('button', { name: 'Delete chat Hiring pipeline' }));
    let dialog = await screen.findByRole('dialog', { name: 'Delete chat?' });
    expect(within(dialog).getByText('Delete chat "Hiring pipeline"?')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(conversationApi.delete).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Delete chat Hiring pipeline' }));
    dialog = await screen.findByRole('dialog', { name: 'Delete chat?' });
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(conversationApi.delete).toHaveBeenCalledWith(5));
    expect(conversationApi.get).not.toHaveBeenCalled();
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('marks conversations Janus started, and clears New once one is opened (JAN-319)', async () => {
    const user = userEvent.setup();
    const janus = {
      id: 9,
      title: 'Your flight moved',
      created_at: '2026-10-11T06:00:00Z',
      updated_at: '2026-10-11T06:00:00Z',
      origin: 'janus',
    };
    const mine = { id: 8, title: 'My own chat', created_at: '2026-10-10T06:00:00Z', updated_at: '2026-10-10T06:00:00Z', origin: 'user', unread: false };
    vi.mocked(conversationApi.list)
      .mockResolvedValueOnce({ data: [{ ...janus, unread: true }, mine] } as never)
      .mockResolvedValue({ data: [{ ...janus, unread: false }, mine] } as never);
    vi.mocked(conversationApi.get).mockResolvedValue({
      data: {
        ...janus,
        unread: false,
        messages: [{ id: 1, role: 'assistant', content: 'Should I update the calendar?', created_at: janus.created_at }],
      },
    } as never);
    renderChat();

    const entry = (await screen.findByText('Your flight moved')).closest('[role="button"]') as HTMLElement;
    expect(within(entry).getByText('Started by Janus')).toBeInTheDocument();
    expect(within(entry).getByText('New')).toBeInTheDocument();
    const own = screen.getByText('My own chat').closest('[role="button"]') as HTMLElement;
    expect(within(own).queryByText('Started by Janus')).not.toBeInTheDocument();
    expect(screen.queryByTestId('janus-started-note')).not.toBeInTheDocument();

    await user.click(entry);

    expect(await screen.findByText('Should I update the calendar?')).toBeInTheDocument();
    expect(await screen.findByTestId('janus-started-note')).toHaveTextContent('Janus started this conversation');
    await waitFor(() => expect(within(entry).queryByText('New')).not.toBeInTheDocument());
    expect(within(entry).getByText('Started by Janus')).toBeInTheDocument();
  });

  it("shows the open conversation's delegated tasks above the transcript (JAN-208)", async () => {
    vi.mocked(delegatedTasksApi.list).mockResolvedValue({
      data: {
        items: [
          {
            delegation_id: 'del_1',
            status: 'running',
            objective: 'Collect this week’s invoices',
            requested_tool_ids: ['emails'],
            allowed_tool_ids: ['emails'],
            result: null,
            error: null,
            depth: 1,
            created_at: '2026-10-10T09:00:00Z',
            claimed_at: '2026-10-10T09:00:01Z',
            completed_at: null,
          },
        ],
        total: 1,
        limit: 10,
        offset: 0,
        has_more: false,
      },
    } as never);
    renderChat();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    expect(delegatedTasksApi.list).not.toHaveBeenCalled();

    act(() => FakeWebSocket.instances[0].emit('__CONVERSATION_ID__:7'));

    const panel = await screen.findByTestId('delegated-tasks');
    expect(delegatedTasksApi.list).toHaveBeenCalledWith(7, { limit: 10 });
    expect(within(panel).getByText('Delegated tasks (1)')).toBeInTheDocument();
    expect(within(panel).getByText('1 active')).toBeInTheDocument();
    expect(within(panel).getByText('Collect this week’s invoices')).toBeInTheDocument();
  });

  it('shows the Janus character instead of an agent picker', async () => {
    vi.mocked(characterApi.get).mockResolvedValue({ data: { name: 'Ada' } } as never);
    renderChat();

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));

    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(await screen.findByText('Ada')).toBeInTheDocument();
  });

  it('consumes __TOOL_SELECTION__ events as lifecycle state, never as chat text', async () => {
    renderChat();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.emit(
        '__TOOL_SELECTION__:' +
          JSON.stringify({
            task_id: 'chat-conversation-1',
            step_id: 'chat-conversation-1::message-1',
            tools: [{ id: 'core', confirmation: 'never' }],
            denied: [{ tool_id: 'github', reason: 'disabled_by_context' }],
          })
      );
      socket.emit('__START__');
      socket.emit('Hello there');
      socket.emit('__END__');
    });

    // The raw event must never leak into the transcript as assistant text.
    expect(screen.queryByText(/__TOOL_SELECTION__/)).not.toBeInTheDocument();
    expect(screen.getByText('Hello there')).toBeInTheDocument();

    // Denied tools are surfaced as display-only lifecycle state.
    expect(await screen.findByText(/Tools unavailable this turn/i)).toBeInTheDocument();
    expect(screen.getByText(/github \(disabled by context\)/)).toBeInTheDocument();
  });

  it('opts into structured tool events and keeps each call as a chip on the reply (JAN-249)', async () => {
    const user = userEvent.setup();
    renderChat();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    expect(socket.url).toContain('tool_events=1');

    const toolEvent = (phase: string, extra: Record<string, unknown> = {}) =>
      '__TOOL_EVENT__:' +
      JSON.stringify({
        call_id: 'run-1',
        tool_id: 'mail',
        tool_name: 'search_emails',
        phase,
        args: { query: 'invoice', password: '[redacted]' },
        args_truncated: false,
        result_preview: null,
        result_truncated: false,
        duration_ms: null,
        ...extra,
      });

    act(() => {
      socket.emit('__START__');
      socket.emit(toolEvent('start'));
      socket.emit('__TOOL_CALL__:search_emails');
    });
    expect(screen.getByText('Running tool search_emails.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'search_emails, running' })).toBeInTheDocument();

    act(() => {
      socket.emit(toolEvent('end', { result_preview: '**2** mails', duration_ms: 12 }));
      socket.emit('__TOOL_RESULT__:search_emails');
      socket.emit('Two invoices arrived.');
      socket.emit('__END__');
    });

    // Neither the structured frame nor the deprecated markers become chat text.
    expect(screen.queryByText(/__TOOL_/)).not.toBeInTheDocument();
    expect(screen.queryByText('Running tool search_emails.')).not.toBeInTheDocument();
    expect(screen.getByText('Two invoices arrived.')).toBeInTheDocument();

    const chip = screen.getByRole('button', { name: 'search_emails, done, 12 ms' });
    await user.click(chip);
    const details = screen.getByRole('region', { name: 'Tool call search_emails' });
    // The result is plain text, not Markdown.
    expect(within(details).getByText('**2** mails')).toBeInTheDocument();
    expect(within(details).getByText(/"password": "\[redacted\]"/)).toBeInTheDocument();
  });

  it('shows the turn steps live in the collapsed timeline, with a waiting approval as waiting (JAN-316)', async () => {
    const user = userEvent.setup();
    renderChat();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];

    const planEvent = (stepId: string, index: number, status: string, toolNames: string[]) =>
      '__PLAN_EVENT__:' + JSON.stringify({ step_id: stepId, index, status, tool_names: toolNames });
    const toolEvent = (callId: string, toolName: string, phase: string, stepId: string) =>
      '__TOOL_EVENT__:' +
      JSON.stringify({
        call_id: callId,
        tool_id: null,
        tool_name: toolName,
        phase,
        args: {},
        args_truncated: false,
        result_preview: phase === 'start' ? null : 'ok',
        result_truncated: false,
        duration_ms: phase === 'start' ? null : 3,
        step_id: stepId,
      });

    const toggle = screen.getByRole('button', { name: /This turn/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await user.click(toggle);

    act(() => {
      socket.emit('__START__');
      socket.emit(planEvent('step-1', 1, 'pending', ['list_memory_facts']));
      socket.emit(planEvent('step-1', 1, 'running', ['list_memory_facts']));
      socket.emit(toolEvent('run-1', 'list_memory_facts', 'start', 'step-1'));
      socket.emit(toolEvent('run-1', 'list_memory_facts', 'end', 'step-1'));
      socket.emit(planEvent('step-1', 1, 'completed', ['list_memory_facts']));
      socket.emit(planEvent('step-2', 2, 'pending', ['send_email']));
      socket.emit(planEvent('step-2', 2, 'running', ['send_email']));
      socket.emit(toolEvent('run-2', 'send_email', 'start', 'step-2'));
      socket.emit(
        '__TOOL_APPROVAL__:' +
          JSON.stringify({
            call_id: 'run-2',
            tool_id: 'mail.send',
            tool_name: 'send_email',
            display_name: 'Send mail',
            risk_level: 'high',
            reason: 'Sends mail',
            args: {},
            args_truncated: false,
            timeout_seconds: 300,
          })
      );
    });

    const timeline = screen.getByTestId('turn-timeline');
    let steps = within(timeline).getAllByTestId('turn-timeline-step');
    expect(steps).toHaveLength(2);
    expect(steps[0]).toHaveTextContent('completed');
    expect(steps[1]).toHaveTextContent('waiting for approval');
    // Step frames are never chat text.
    expect(screen.queryByText(/__PLAN_EVENT__/)).not.toBeInTheDocument();

    act(() => {
      socket.emit(toolEvent('run-2', 'send_email', 'end', 'step-2'));
      socket.emit(planEvent('step-2', 2, 'completed', ['send_email']));
      socket.emit('Sent.');
      socket.emit('__END__');
    });
    steps = within(timeline).getAllByTestId('turn-timeline-step');
    expect(steps[1]).toHaveTextContent('completed');
  });

  it('fails a step left open when the socket closes mid-turn (JAN-316)', async () => {
    const user = userEvent.setup();
    renderChat();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    await user.click(screen.getByRole('button', { name: /This turn/ }));

    act(() => {
      socket.emit('__START__');
      socket.emit('__PLAN_EVENT__:' + JSON.stringify({ step_id: 'step-1', index: 1, status: 'running', tool_names: ['a'] }));
    });
    expect(screen.getByTestId('turn-timeline-step')).toHaveTextContent('running');

    act(() => socket.close());
    expect(screen.getByTestId('turn-timeline-step')).toHaveTextContent('failed');
  });

  it('shows a refused tool call as blocked', async () => {
    renderChat();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.emit('__START__');
      socket.emit(
        '__TOOL_EVENT__:' +
          JSON.stringify({
            call_id: 'run-9',
            tool_id: 'mail.send',
            tool_name: 'send_email',
            phase: 'blocked',
            args: { to: 'a@example.com' },
            args_truncated: false,
            result_preview: 'BLOCKED: send_email was not run because it is not available. Nothing was changed.',
            result_truncated: false,
            duration_ms: 2,
          })
      );
      socket.emit('I could not send that mail.');
      socket.emit('__END__');
    });

    expect(screen.getByRole('button', { name: 'send_email, blocked, 2 ms' })).toBeInTheDocument();
  });

  it('shows the stored tool calls of a reloaded conversation', async () => {
    window.sessionStorage.setItem(STORAGE_KEY, '8');
    vi.mocked(conversationApi.get).mockResolvedValue({
      data: {
        id: 8,
        title: 'Inbox',
        created_at: '2026-09-28T08:00:00Z',
        updated_at: '2026-09-28T08:05:00Z',
        messages: [
          { role: 'user', content: 'Any invoices?', tool_calls: [] },
          {
            role: 'assistant',
            content: 'Two invoices arrived.',
            tool_calls: [
              {
                call_id: 'run-1',
                tool_id: 'mail',
                tool_name: 'search_emails',
                phase: 'end',
                args: { query: 'invoice' },
                args_truncated: false,
                result_preview: '2 mails',
                result_truncated: true,
                duration_ms: 1500,
              },
              { call_id: '', tool_name: 'malformed', phase: 'nope' },
            ],
          },
        ],
      },
    } as never);

    renderChat();

    expect(await screen.findByText('Two invoices arrived.')).toBeInTheDocument();
    const chips = within(screen.getByRole('list', { name: 'Tool calls' })).getAllByRole('button');
    expect(chips.map((chip) => chip.getAttribute('aria-label'))).toEqual(['search_emails, done, 1.5 s']);
  });

  it('keeps the /chat transcript in its own scroll region, pinned to the newest message (JAN-273)', async () => {
    render(
      <QueryClientProvider client={newQueryClient()}>
        <MemoryRouter initialEntries={['/chat']}>
          <ChatSessionProvider userId={currentUser.id}>
            <ChatPage />
          </ChatSessionProvider>
        </MemoryRouter>
      </QueryClientProvider>
    );
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));

    // The page is bounded by the viewport, and its grid row cannot grow to fit
    // the transcript, so the history scrolls instead of running under the footer.
    const page = screen.getByTestId('chat-page');
    expect(page.className).toMatch(/(^|\s)h-\[calc\(100dvh-/);
    expect(page.querySelector('.grid')?.className).toContain('grid-rows-[minmax(0,1fr)]');

    const history = screen.getByRole('region', { name: 'Conversation history' });
    expect(history.className).toContain('overflow-y-auto');
    expect(history.className).toContain('min-h-0');

    // jsdom has no layout: fake a transcript taller than its box.
    Object.defineProperty(history, 'scrollHeight', { configurable: true, value: 4321 });
    for (let turn = 0; turn < 3; turn += 1) streamReply(latestSocket(), `Reply number ${turn}`);

    expect(screen.getByText('Reply number 2')).toBeInTheDocument();
    expect(history.scrollTop).toBe(4321);
  });

  it('runs a page action on the current page, answers its result, and undoes it from the chip (JAN-251)', async () => {
    const user = userEvent.setup();
    const FilterPage = () => {
      const [priority, setPriority] = useState('all');
      usePageContext(
        { page: 'Emails', filters: { priority }, visible_items: [{ type: 'email', id: 7, label: 'Invoice' }] },
        {
          page: 'emails',
          filters: { priority: { values: ['all', 'important'], get: () => priority, set: setPriority } },
        }
      );
      return <p>Showing {priority}</p>;
    };
    render(
      <MemoryRouter initialEntries={['/emails']}>
        <QueryClientProvider client={newQueryClient()}>
          <PageContextProvider>
            <UIActionProvider>
              <ChatSessionProvider userId={currentUser.id}>
                <FilterPage />
                <BriefingChat />
              </ChatSessionProvider>
            </UIActionProvider>
          </PageContextProvider>
        </QueryClientProvider>
      </MemoryRouter>
    );
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = latestSocket();
    const call = {
      call_id: 'run-ui',
      tool_id: 'ui_actions',
      tool_name: 'ui_set_filter',
      args: { page: 'emails', key: 'priority', value: 'important' },
      args_truncated: false,
      result_truncated: false,
    };
    const action = (actionId: string, params: Record<string, unknown>, verb = 'set_filter') =>
      '__UI_ACTION__:' + JSON.stringify({ action_id: actionId, call_id: 'run-ui', action: verb, params, label: 'x' });

    act(() => {
      socket.emit('__START__');
      socket.emit('__TOOL_EVENT__:' + JSON.stringify({ ...call, phase: 'start', result_preview: null, duration_ms: null }));
      socket.emit(action('a-1', { page: 'emails', key: 'priority', value: 'important' }));
    });
    await waitFor(() =>
      expect(socket.sent.map((frame) => JSON.parse(frame))).toContainEqual({
        type: 'ui_action_result',
        action_id: 'a-1',
        ok: true,
        error: null,
      })
    );
    expect(screen.getByText('Showing important')).toBeInTheDocument();
    // The frame never becomes reply text.
    expect(screen.queryByText(/__UI_ACTION__/)).not.toBeInTheDocument();

    act(() => {
      socket.emit('__TOOL_EVENT__:' + JSON.stringify({ ...call, phase: 'end', result_preview: 'Done.', duration_ms: 9 }));
      socket.emit('Filtered.');
      socket.emit('__END__');
    });
    await user.click(screen.getByRole('button', { name: 'Undo set_filter · priority = important' }));
    expect(screen.getByText('Showing all')).toBeInTheDocument();
    expect(screen.getByText('undone')).toBeInTheDocument();

    // A route outside the allowlist is rejected and reported, not followed.
    act(() => socket.emit(action('a-2', { route: 'https://evil.example.com' }, 'navigate')));
    await waitFor(() =>
      expect(socket.sent.map((frame) => JSON.parse(frame))).toContainEqual({
        type: 'ui_action_result',
        action_id: 'a-2',
        ok: false,
        error: 'unknown route https://evil.example.com',
      })
    );
  });

  it('reports that a view without the page-action provider cannot perform page actions', async () => {
    renderChat();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = latestSocket();
    act(() =>
      socket.emit('__UI_ACTION__:' + JSON.stringify({ action_id: 'a-9', call_id: 'c', action: 'scroll_to', params: {} }))
    );
    await waitFor(() =>
      expect(socket.sent.map((frame) => JSON.parse(frame))).toContainEqual({
        type: 'ui_action_result',
        action_id: 'a-9',
        ok: false,
        error: 'this view cannot perform page actions',
      })
    );
  });
});

describe('BriefingChat UI introspection (JAN-285)', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket);
    vi.mocked(conversationApi.list).mockResolvedValue({ data: [] } as never);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('answers snapshot and styles requests from the page, never from the chat panel', async () => {
    render(
      <QueryClientProvider client={newQueryClient()}>
        <ChatSessionProvider userId={currentUser.id}>
          <main>
            <h1>Emails</h1>
            <button disabled>Archive</button>
          </main>
          <BriefingChat />
        </ChatSessionProvider>
      </QueryClientProvider>
    );
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = latestSocket();
    const request = (requestId: string, kind: string, params: Record<string, string> = {}) =>
      '__UI_INTROSPECT__:' + JSON.stringify({ request_id: requestId, kind, params, timeout_seconds: 10 });
    const answers = () =>
      socket.sent.map((frame) => JSON.parse(frame)).filter((frame) => frame.type === 'ui_introspection_result');

    act(() => socket.emit(request('r-1', 'snapshot')));
    await waitFor(() => expect(answers()).toHaveLength(1));
    const [snapshot] = answers();
    expect(snapshot).toMatchObject({ request_id: 'r-1', ok: true, error: null });
    const archive = snapshot.result.nodes.find((node: { name?: string }) => node.name === 'Archive');
    expect(archive).toMatchObject({ role: 'button', disabled: true });
    // The chat panel itself (its heading, input, and buttons) is never part of the outline.
    expect(snapshot.result.nodes.every((node: { name?: string }) => !/chat/i.test(node.name ?? ''))).toBe(true);
    expect(screen.queryByText(/__UI_INTROSPECT__/)).not.toBeInTheDocument();

    act(() => socket.emit(request('r-2', 'element_styles', { ref: archive.ref })));
    await waitFor(() => expect(answers()).toHaveLength(2));
    expect(answers()[1]).toMatchObject({ request_id: 'r-2', ok: true, result: { ref: archive.ref, tag: 'button' } });

    act(() => socket.emit(request('r-3', 'element_styles', { ref: 'e999' })));
    await waitFor(() => expect(answers()).toHaveLength(3));
    expect(answers()[2]).toMatchObject({ request_id: 'r-3', ok: false, result: null });
  });
});

describe('BriefingChat UI blocks (JAN-252)', () => {
  const card = { id: 'blk_card00000001', version: 1, type: 'card', title: 'Invoice overdue', body: '<i>120 EUR</i>' };
  const actions = {
    id: 'blk_action000001',
    version: 1,
    type: 'actions',
    buttons: [{ label: 'Snooze', tool: 'snooze_topic', args: { topic_id: 4 }, style: 'primary' }],
  };

  const renderRoutedChat = () =>
    render(
      <QueryClientProvider client={newQueryClient()}>
        <MemoryRouter>
          <ChatSessionProvider userId={currentUser.id}>
            <BriefingChat />
          </ChatSessionProvider>
        </MemoryRouter>
      </QueryClientProvider>
    );

  beforeEach(() => {
    window.sessionStorage.clear();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket);
    vi.mocked(conversationApi.list).mockResolvedValue({ data: [] } as never);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('renders streamed blocks under the reply, never as text, and keeps a broken one as JSON', async () => {
    renderRoutedChat();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.emit('__START__');
      socket.emit('Here it is:');
      socket.emit(`__UI_BLOCK__:${JSON.stringify(card)}`);
      socket.emit(`__UI_BLOCK__:${JSON.stringify({ ...card, version: 99 })}`);
      socket.emit('__UI_BLOCK__:{not json');
      socket.emit('__END__');
    });

    expect(screen.getByText('Here it is:')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Card' })).toHaveTextContent('<i>120 EUR</i>');
    expect(screen.getByRole('region', { name: 'Unrenderable block' })).toHaveTextContent('"version": 99');
    expect(screen.queryByText(/__UI_BLOCK__/)).not.toBeInTheDocument();
  });

  it('sends an action button as an ordinary chat message, leaving the draft alone', async () => {
    const user = userEvent.setup();
    renderRoutedChat();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    await user.type(screen.getByPlaceholderText('Ask a question...'), 'half-typed');

    act(() => {
      socket.emit('__START__');
      socket.emit(`__UI_BLOCK__:${JSON.stringify(actions)}`);
      socket.emit('__END__');
    });
    await user.click(screen.getByRole('button', { name: 'Snooze' }));

    const text = 'Run "Snooze": call snooze_topic with {"topic_id":4}';
    expect(socket.sent.map((frame) => JSON.parse(frame))).toEqual([
      { type: 'message', text, page_context: expect.objectContaining({ route: '/' }) },
    ]);
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Ask a question...')).toHaveValue('half-typed');
  });

  it('renders the stored blocks of a reloaded conversation', async () => {
    window.sessionStorage.setItem(STORAGE_KEY, '9');
    vi.mocked(conversationApi.get).mockResolvedValue({
      data: {
        id: 9,
        title: 'Invoices',
        created_at: '2026-10-05T08:00:00Z',
        updated_at: '2026-10-05T08:05:00Z',
        messages: [
          { role: 'user', content: 'Show the invoice', tool_calls: [], ui_blocks: [] },
          {
            role: 'assistant',
            content: 'Here it is.',
            tool_calls: [],
            ui_blocks: [{ block_id: card.id, block_type: 'card', schema_version: 1, payload: card }],
          },
        ],
      },
    } as never);

    renderRoutedChat();

    expect(await screen.findByText('Here it is.')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Card' })).toHaveTextContent('Invoice overdue');
  });
});
