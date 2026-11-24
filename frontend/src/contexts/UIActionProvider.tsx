import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { currentPageContext, usePageContextStore } from './pageContext';
import {
  createUIActionStore,
  UIActionLogContext,
  UIActionRuntimeContext,
  type ExecutedUIAction,
  type UIActionEnv,
  type UIActionLog,
  type UIActionRequest,
  type UIActionRuntime,
} from './uiActions';

const HIGHLIGHT_CLASS = 'ui-action-highlight';
const HIGHLIGHT_MS = 4000;

/** The element carrying `attribute="value"`; compared as text, never built into a selector. */
const findMarked = (attribute: string, value: string): HTMLElement | null =>
  Array.from(document.querySelectorAll<HTMLElement>(`[${attribute}]`)).find(
    (element) => element.getAttribute(attribute) === value
  ) ?? null;

const highlightElement = (type: string, id: string | number): boolean => {
  const element = findMarked('data-ui-item', `${type}:${id}`);
  if (!element) return false;
  element.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
  element.classList.add(HIGHLIGHT_CLASS);
  window.setTimeout(() => element.classList.remove(HIGHLIGHT_CLASS), HIGHLIGHT_MS);
  return true;
};

const scrollToSection = (section: string): boolean => {
  const element = findMarked('data-ui-section', section);
  element?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  return element != null;
};

const currentRoute = () => `${window.location.pathname}${window.location.search}`;

/**
 * Holds the tab's page-action registry (JAN-251) and runs the `__UI_ACTION__`
 * requests `ChatSessionProvider` hands it; mounted inside `PageContextProvider`
 * (it validates ids against the published snapshot) and the router (it navigates).
 * The log of executed actions feeds the transcript chips and their undo.
 */
export const UIActionProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [store] = useState(createUIActionStore);
  const [executed, setExecuted] = useState<Record<string, ExecutedUIAction>>({});
  const pageContextStore = usePageContextStore();
  // The router's navigate changes with the location; the runtime stays stable for the socket.
  const navigate = useNavigate();
  const navigateRef = useRef(navigate);
  useEffect(() => {
    navigateRef.current = navigate;
  }, [navigate]);

  const perform = useCallback(
    async (request: UIActionRequest) => {
      const env: UIActionEnv = {
        navigate: (route) => navigateRef.current(route),
        currentRoute,
        snapshot: () => currentPageContext(pageContextStore),
        highlight: highlightElement,
        scrollTo: scrollToSection,
      };
      const outcome = await store.execute(request, env);
      if (request.call_id) {
        const entry: ExecutedUIAction = { action: request.action, label: outcome.label, ok: outcome.ok, undone: false };
        if (outcome.ok && outcome.undo) entry.undo = outcome.undo;
        setExecuted((previous) => ({ ...previous, [request.call_id]: entry }));
      }
      return outcome;
    },
    [pageContextStore, store]
  );

  const undo = useCallback(
    (callId: string) => {
      const entry = executed[callId];
      if (!entry?.undo || entry.undone) return;
      entry.undo();
      setExecuted((previous) => ({ ...previous, [callId]: { ...entry, undone: true } }));
    },
    [executed]
  );

  const runtime = useMemo<UIActionRuntime>(() => ({ store, perform }), [perform, store]);
  const log = useMemo<UIActionLog>(() => ({ executed, undo }), [executed, undo]);

  return (
    <UIActionRuntimeContext.Provider value={runtime}>
      <UIActionLogContext.Provider value={log}>{children}</UIActionLogContext.Provider>
    </UIActionRuntimeContext.Provider>
  );
};
