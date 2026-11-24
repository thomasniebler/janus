import { useEffect } from 'react';
import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { characterApi } from '../../api';
import {
  FakeWebSocket,
  neverResolving,
  withApiOverride,
  withFakeWebSocket,
  withQueryData,
} from '../../test/storybookQueryClient';
import { withChatSession } from '../../test/storybookChatSession';
import { BriefingChat } from './BriefingChat';
import type { CharacterProfile, ConversationSummary } from '../../types';

const character: CharacterProfile = {
  constitution_id: 'janus-default',
  version: 1,
  name: 'Janus',
  identity: 'A calm, local-first assistant that keeps email, calendar, and tasks in view.',
  core_values: ['Be accurate', 'Protect privacy'],
  non_negotiables: ['Never send mail without confirmation'],
  traits: [],
  exploration_rate: 0.2,
  additional_instructions: null,
  memory_instructions: null,
};

const conversations: ConversationSummary[] = [
  { id: 3, title: 'Q3 roadmap follow-up', created_at: '2026-09-25T09:00:00Z', updated_at: '2026-09-25T09:04:00Z' },
  { id: 2, title: 'Hiring pipeline check-in', created_at: '2026-09-24T15:00:00Z', updated_at: '2026-09-24T15:20:00Z' },
  { id: 1, title: null, created_at: '2026-09-20T08:00:00Z', updated_at: '2026-09-20T08:00:00Z' },
];

const seedWithCharacter =
  (overrides: { conversations?: ConversationSummary[] } = {}) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['character-profile'], character);
    queryClient.setQueryData(['conversations'], overrides.conversations ?? conversations);
  };

const meta = {
  title: 'Features/BriefingChat',
  component: BriefingChat,
  decorators: [withFakeWebSocket()],
} satisfies Meta<typeof BriefingChat>;

export default meta;

type Story = StoryObj<typeof meta>;

export const LoadingCharacter: Story = {
  decorators: [withChatSession(), withQueryData(), withApiOverride(characterApi, 'get', () => neverResolving())],
};

export const EmptyConversationList: Story = {
  decorators: [withChatSession(), withQueryData(seedWithCharacter({ conversations: [] }))],
};

export const ConversationHistory: Story = {
  decorators: [withChatSession(), withQueryData(seedWithCharacter())],
};

/** Conversations a trigger run started (JAN-319): marked "Started by Janus", and "New" until opened. */
export const JanusStartedConversations: Story = {
  decorators: [
    withChatSession(),
    withQueryData(
      seedWithCharacter({
        conversations: [
          {
            id: 7,
            title: 'Your flight to Lisbon moved',
            created_at: '2026-09-26T06:30:00Z',
            updated_at: '2026-09-26T06:30:00Z',
            origin: 'janus',
            unread: true,
          },
          {
            id: 6,
            title: 'Invoice from Acme is overdue',
            created_at: '2026-09-25T18:00:00Z',
            updated_at: '2026-09-25T18:05:00Z',
            origin: 'janus',
            unread: false,
          },
          ...conversations.map((conversation) => ({ ...conversation, origin: 'user' as const, unread: false })),
        ],
      })
    ),
  ],
};

/** Drives the socket through a full user/assistant exchange after mount. */
const LiveExchange: React.FC = () => {
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
      socket?.emit('__CONVERSATION_ID__:42');
      socket?.emit('__START__');
      socket?.emit('Two meetings today and nothing overdue — no action needed before 14:00.');
      socket?.emit('__END__');
    }, 10);
    return () => window.clearTimeout(timer);
  }, []);
  return <BriefingChat />;
};

export const ActiveConversation: Story = {
  decorators: [withChatSession(), withQueryData(seedWithCharacter())],
  render: () => <LiveExchange />,
};

const toolEvent = (call: Record<string, unknown>) =>
  '__TOOL_EVENT__:' +
  JSON.stringify({ args_truncated: false, result_truncated: false, result_preview: null, duration_ms: null, ...call });

/** A reply that used tools: one call succeeded, one was refused by the tool policy (JAN-249). */
const ExchangeWithToolCalls: React.FC = () => {
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
      const search = { call_id: 'run-1', tool_id: 'mail', tool_name: 'search_emails', args: { query: 'invoice' } };
      const send = { call_id: 'run-2', tool_id: 'mail.send', tool_name: 'send_email', args: { to: 'billing@example.com' } };
      socket?.emit('__CONVERSATION_ID__:43');
      socket?.emit('__START__');
      socket?.emit(toolEvent({ ...search, phase: 'start' }));
      socket?.emit(toolEvent({ ...search, phase: 'end', result_preview: '2 invoices found', duration_ms: 380 }));
      socket?.emit(
        toolEvent({
          ...send,
          phase: 'blocked',
          result_preview: 'BLOCKED: send_email was not run because the integration it needs is not connected.',
          duration_ms: 2,
        })
      );
      socket?.emit('Two invoices arrived. I could not reply: sending mail is not connected.');
      socket?.emit('__END__');
    }, 10);
    return () => window.clearTimeout(timer);
  }, []);
  return <BriefingChat />;
};

export const ConversationWithToolCalls: Story = {
  decorators: [withChatSession(), withQueryData(seedWithCharacter())],
  render: () => <ExchangeWithToolCalls />,
};

/** A reply that cross-references wiki pages: `[[Page Title]]` renders as a link to the Wiki tab (JAN-331). */
const ExchangeWithWikiLinks: React.FC = () => {
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
      socket?.emit('__CONVERSATION_ID__:44');
      socket?.emit('__START__');
      socket?.emit('The renewal is tracked on [[Acme renewal]]; [[Dana Whitfield|Dana]] owns the draft.');
      socket?.emit('__END__');
    }, 10);
    return () => window.clearTimeout(timer);
  }, []);
  return (
    <MemoryRouter>
      <BriefingChat />
    </MemoryRouter>
  );
};

export const ReplyWithWikiLinks: Story = {
  decorators: [withChatSession(), withQueryData(seedWithCharacter())],
  render: () => <ExchangeWithWikiLinks />,
};

export const CompactSidebarVariant: Story = {
  args: { variant: 'compactSidebar' },
  decorators: [withChatSession(), withQueryData(seedWithCharacter())],
  parameters: { layout: 'padded' },
  render: (args) => (
    <div className="flex h-[520px] w-[24rem] flex-col rounded-xl border border-gray-200 p-4 dark:border-gray-800">
      <BriefingChat {...args} />
    </div>
  ),
};
