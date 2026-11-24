import { useCallback, useContext, useEffect, useRef, useState } from 'react';

import {
  parseUIAction,
  UI_ACTION_PREFIX,
  UIActionRuntimeContext,
  type PageActions,
  type UIActionOutcome,
} from '../contexts/uiActions';
import { LEGACY_TOOL_MARKER_PREFIXES } from '../lib/toolCallEvents';
import {
  createIntrospectionSession,
  parseIntrospectionRequest,
  UI_INTROSPECTION_PREFIX,
  UI_INTROSPECTION_RESULT_TYPE,
  type IntrospectionSession,
} from '../lib/uiIntrospection';

/**
 * Registers what the calling page (or section) lets Janus do through page
 * actions (JAN-251) for as long as it is mounted: its filters with their
 * allowed values, the item types it can open or highlight, its section ids,
 * and the forms it lets Janus prefill. Pass `null` to register nothing.
 * Re-registered after every render, so the handlers always see current state.
 */
export const usePageActions = (actions: PageActions | null): void => {
  const runtime = useContext(UIActionRuntimeContext);
  const [owner] = useState(() => Symbol('page-actions'));
  const store = runtime?.store;

  useEffect(() => {
    if (!store) return;
    if (actions) store.register(owner, actions);
    else store.clear(owner);
  });

  useEffect(() => () => store?.clear(owner), [owner, store]);
};

const NO_RUNTIME: UIActionOutcome = { ok: false, error: 'this view cannot perform page actions', label: '' };

/**
 * For `ChatSessionProvider`: consumes the `/ws/chat` frames that are neither
 * reply text nor session state. A `__UI_ACTION__` frame (JAN-251) is run by the
 * `UIActionProvider` and answered on `socket` with
 * `{"type":"ui_action_result","action_id","ok","error"}`; a `__UI_INTROSPECT__`
 * frame (JAN-285) is answered from the page with
 * `{"type":"ui_introspection_result","request_id","ok","result","error"}`; the
 * deprecated `__TOOL_CALL__`/`__TOOL_RESULT__` markers are dropped. Returns
 * whether the frame was one of these. The callback is stable for the socket's lifetime.
 */
export const useChatSideFrames = (): ((message: string, socket: WebSocket) => boolean) => {
  const runtime = useContext(UIActionRuntimeContext);
  const runtimeRef = useRef(runtime);
  useEffect(() => {
    runtimeRef.current = runtime;
  }, [runtime]);
  // The latest snapshot's refs, for `element_styles` requests of this chat session.
  const introspectionRef = useRef<IntrospectionSession | null>(null);

  return useCallback((message: string, socket: WebSocket) => {
    if (LEGACY_TOOL_MARKER_PREFIXES.some((prefix) => message.startsWith(prefix))) return true;
    if (message.startsWith(UI_INTROSPECTION_PREFIX)) {
      const request = parseIntrospectionRequest(message.slice(UI_INTROSPECTION_PREFIX.length));
      if (!request || socket.readyState !== WebSocket.OPEN) return true;
      introspectionRef.current ??= createIntrospectionSession();
      const answer = introspectionRef.current.answer(request);
      socket.send(
        JSON.stringify({
          type: UI_INTROSPECTION_RESULT_TYPE,
          request_id: request.request_id,
          ok: answer.ok,
          result: answer.result ?? null,
          error: answer.error ?? null,
        })
      );
      return true;
    }
    if (!message.startsWith(UI_ACTION_PREFIX)) return false;
    const request = parseUIAction(message.slice(UI_ACTION_PREFIX.length));
    if (!request) return true;
    const outcome = runtimeRef.current ? runtimeRef.current.perform(request) : Promise.resolve(NO_RUNTIME);
    void outcome.then((result) => {
      if (socket.readyState !== WebSocket.OPEN) return;
      socket.send(
        JSON.stringify({ type: 'ui_action_result', action_id: request.action_id, ok: result.ok, error: result.error ?? null })
      );
    });
    return true;
  }, []);
};
