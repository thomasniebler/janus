import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';

import { usePageContextStore } from '../contexts/pageContext';
import type { PageActions } from '../contexts/uiActions';
import type { PageSectionInput, PageSnapshotInput } from '../lib/pageContext';
import { usePageActions } from './useUIActions';

/**
 * Publishes what the current page shows, for the next chat message (JAN-250).
 * Pass only what is visible: ids and short labels (`pageItem`), the open item
 * as `selection`, the active tab/filter/search as `filters`. The route is
 * added here, and the snapshot is withdrawn when the page unmounts.
 *
 * `actions` registers what Janus may do on this page through page actions
 * (JAN-251, `usePageActions`); the ids it acts on are checked against this snapshot.
 */
export const usePageContext = (snapshot: PageSnapshotInput, actions: PageActions | null = null): void => {
  const store = usePageContextStore();
  const { pathname, search } = useLocation();
  const [owner] = useState(() => Symbol('page-context'));
  // Re-publish only when the content changes, not on every render.
  const serialized = JSON.stringify(snapshot);

  useEffect(() => {
    if (!store) return;
    store.publishPage(owner, { ...(JSON.parse(serialized) as PageSnapshotInput), route: `${pathname}${search}` });
  }, [owner, pathname, search, serialized, store]);

  useEffect(() => () => store?.clear(owner), [owner, store]);
  usePageActions(actions);
};

/**
 * Adds a nested section's visible items, filters, and selection to the page's
 * snapshot, for feature components that own the data a page shows, plus the
 * page actions the section supports (JAN-251).
 */
export const usePageContextSection = (section: PageSectionInput, actions: PageActions | null = null): void => {
  const store = usePageContextStore();
  const [owner] = useState(() => Symbol('page-context-section'));
  const serialized = JSON.stringify(section);

  useEffect(() => {
    store?.publishSection(owner, JSON.parse(serialized) as PageSectionInput);
  }, [owner, serialized, store]);

  useEffect(() => () => store?.clear(owner), [owner, store]);
  usePageActions(actions);
};
