import { useMemo } from 'react';
import { Book, Brain, CalendarDays, LayoutDashboard, ListChecks, Mail, MessageSquare, Settings } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useOpenProposalCount } from '../features/triggers/triggerProposalCount';
import { newConversationsBadgeLabel, useNewJanusConversationCount } from '../features/janusConversations';

export interface NavItem {
  name: string;
  path: string;
  icon: LucideIcon;
  /** Count shown as a badge next to the entry (0 or absent: none). */
  badge?: number;
  /** Spoken label for `badge`. */
  badgeLabel?: string;
}

const NAV_ITEMS: NavItem[] = [
  { name: 'Dashboard', path: '/', icon: LayoutDashboard },
  { name: 'Chat', path: '/chat', icon: MessageSquare },
  { name: 'Memory & Knowledge', path: '/memory', icon: Brain },
  { name: 'Agent Tasks', path: '/tasks', icon: ListChecks },
  { name: 'Planner', path: '/planner', icon: CalendarDays },
  { name: 'Email Preview', path: '/emails', icon: Mail },
  { name: 'Settings', path: '/settings', icon: Settings },
  { name: 'Documentation', path: '/docs', icon: Book },
];

/** Spoken label for the suggested-automations count (JAN-235). */
export const proposalBadgeLabel = (count: number): string =>
  `${count} suggested automation${count === 1 ? '' : 's'} awaiting review`;

/**
 * Shared primary navigation used by all three shells. Settings carries the
 * count of automations Janus suggested that await review (they live on
 * Settings -> Triggers); Chat carries the count of conversations Janus started
 * that are still unread (JAN-319).
 */
export const useNavItems = (): NavItem[] => {
  const openProposals = useOpenProposalCount();
  const newConversations = useNewJanusConversationCount();
  return useMemo(
    () =>
      NAV_ITEMS.map((item) => {
        if (item.path === '/settings' && openProposals > 0) {
          return { ...item, badge: openProposals, badgeLabel: proposalBadgeLabel(openProposals) };
        }
        if (item.path === '/chat' && newConversations > 0) {
          return { ...item, badge: newConversations, badgeLabel: newConversationsBadgeLabel(newConversations) };
        }
        return item;
      }),
    [openProposals, newConversations]
  );
};

/** True when `pathname` is inside the route owned by `item`. */
export const isNavItemActive = (item: NavItem, pathname: string): boolean =>
  pathname === item.path || (item.path !== '/' && pathname.startsWith(item.path));

/**
 * True on the full-width chat page, where shells render no sidebar/inline chat
 * so the tab's chat session is never shown twice (JAN-248).
 */
export const isChatRoute = (pathname: string): boolean => pathname === '/chat' || pathname.startsWith('/chat/');
