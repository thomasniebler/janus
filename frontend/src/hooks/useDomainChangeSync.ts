import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { startDomainChangeSync } from '../lib/domainChanges';
import { buildWebSocketUrl } from '../lib/network';

/**
 * Keep TanStack Query caches in step with backend writes made anywhere — chat
 * tools, trigger runs, other tabs — by subscribing to `/ws/changes` (JAN-104).
 *
 * Mount once per tab for the signed-in user; `userId` re-opens the socket when
 * the session's user changes.
 */
export function useDomainChangeSync(userId: number): void {
  const queryClient = useQueryClient();

  useEffect(
    () => startDomainChangeSync({ queryClient, url: buildWebSocketUrl('/ws/changes') }),
    [queryClient, userId],
  );
}
