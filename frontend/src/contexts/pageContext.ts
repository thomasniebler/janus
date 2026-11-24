import { createContext, useContext } from 'react';

import {
  fitPageContext,
  routePageContext,
  type PageContextItem,
  type PageContextSnapshot,
  type PageSectionInput,
} from '../lib/pageContext';

/**
 * The page-context registry (JAN-250). Pages and sections publish into it from
 * effects; `ChatSessionProvider` reads `current()` when a message is sent. It
 * is a plain mutable object held once per provider, not React state:
 * publishing never re-renders anything.
 */
export interface PageContextStore {
  publishPage: (owner: symbol, snapshot: PageContextSnapshot) => void;
  publishSection: (owner: symbol, section: PageSectionInput) => void;
  clear: (owner: symbol) => void;
  /** The snapshot for the next message: the published page if it matches `pathname`, else route only. */
  current: (pathname: string, search: string) => PageContextSnapshot;
}

const pathOf = (route: string) => route.split(/[?#]/, 1)[0];

export const createPageContextStore = (): PageContextStore => {
  let page: { owner: symbol; snapshot: PageContextSnapshot } | null = null;
  const sections = new Map<symbol, PageSectionInput>();

  return {
    publishPage(owner, snapshot) {
      page = { owner, snapshot };
    },
    publishSection(owner, section) {
      sections.set(owner, section);
    },
    clear(owner) {
      if (page?.owner === owner) page = null;
      sections.delete(owner);
    },
    current(pathname, search) {
      if (!page || pathOf(page.snapshot.route) !== pathname) {
        return routePageContext(pathname, search);
      }
      const merged: PageContextSnapshot = { ...page.snapshot, route: `${pathname}${search}` };
      let filters = { ...(merged.filters ?? {}) };
      let items: PageContextItem[] = [...(merged.visible_items ?? [])];
      for (const section of sections.values()) {
        filters = { ...filters, ...(section.filters ?? {}) };
        items = [...items, ...(section.visible_items ?? [])];
        if (section.selection) merged.selection = section.selection;
      }
      if (Object.keys(filters).length > 0) merged.filters = filters;
      if (items.length > 0) merged.visible_items = items;
      return fitPageContext(merged);
    },
  };
};

/**
 * The snapshot to send with a message right now (JAN-250): the published page
 * for the current location, or the route-only context without a registry.
 */
export const currentPageContext = (store: PageContextStore | null): PageContextSnapshot => {
  const { pathname, search } = window.location;
  return store ? store.current(pathname, search) : fitPageContext(routePageContext(pathname, search));
};

export const PageContextStoreContext = createContext<PageContextStore | null>(null);

/** The registry, or `null` outside a `PageContextProvider` (tests, stories). */
export const usePageContextStore = (): PageContextStore | null => useContext(PageContextStoreContext);
