import { cut, serializeSnapshot, type UISnapshot } from './uiOutline';

/**
 * UI introspection (JAN-282..285): the browser side of the chat-only
 * `ui_introspection` tools. The backend sends a `__UI_INTROSPECT__` frame on
 * `/ws/chat`; `useChatSideFrames` (`src/hooks/useUIActions.ts`) hands it to the
 * chat session's `IntrospectionSession`, which answers from the page the user
 * has open:
 *
 * - `snapshot`: the bounded semantic outline of `uiOutline.ts`, whose refs the
 *   session keeps until the next snapshot;
 * - `element_styles`: the allowlisted computed styles and the bounding box of
 *   one element of the latest snapshot.
 *
 * It only reads; the backend re-sanitizes every answer
 * (`services/ui_introspection.py`).
 */

/** Prefix of the `/ws/chat` introspection request frame. */
export const UI_INTROSPECTION_PREFIX = '__UI_INTROSPECT__:';
/** `type` of the answer frame sent back on the socket. */
export const UI_INTROSPECTION_RESULT_TYPE = 'ui_introspection_result';

const MAX_STYLE_VALUE_CHARS = 120;

/** Computed style properties `element_styles` returns, mirroring the backend's `STYLE_PROPERTIES`. */
export const STYLE_PROPERTIES = [
  'display', 'position', 'visibility', 'opacity', 'z-index', 'overflow', 'overflow-x', 'overflow-y', 'box-sizing',
  'width', 'height', 'min-width', 'min-height', 'max-width', 'max-height', 'margin', 'padding',
  'top', 'right', 'bottom', 'left', 'flex-direction', 'flex-wrap', 'justify-content', 'align-items',
  'grid-template-columns', 'gap', 'color', 'background-color', 'border', 'border-radius', 'outline',
  'font-family', 'font-size', 'font-weight', 'line-height', 'text-align', 'text-overflow', 'white-space',
  'cursor', 'pointer-events', 'transform',
] as const;

type IntrospectionKind = 'snapshot' | 'element_styles';

export interface IntrospectionRequest {
  request_id: string;
  kind: IntrospectionKind;
  params: Record<string, string>;
}

interface ElementStyles {
  ref: string;
  tag: string;
  box: { x: number; y: number; width: number; height: number };
  styles: Record<string, string>;
}

interface IntrospectionAnswer {
  ok: boolean;
  result?: UISnapshot | ElementStyles;
  error?: string;
}

/** Parses a `__UI_INTROSPECT__:` frame body; `null` for malformed payloads. */
export const parseIntrospectionRequest = (body: string): IntrospectionRequest | null => {
  try {
    const value = JSON.parse(body) as Record<string, unknown> | null;
    if (!value || typeof value !== 'object') return null;
    if (typeof value.request_id !== 'string' || !value.request_id) return null;
    if (value.kind !== 'snapshot' && value.kind !== 'element_styles') return null;
    const params: Record<string, string> = {};
    if (value.params && typeof value.params === 'object') {
      for (const [key, item] of Object.entries(value.params as Record<string, unknown>)) {
        if (typeof item === 'string') params[key] = item;
      }
    }
    return { request_id: value.request_id, kind: value.kind, params };
  } catch {
    return null;
  }
};

const round = (value: number) => Math.round(value * 100) / 100;

/** The allowlisted computed styles and bounding box of one element; a `url(...)` value is left out. */
export const elementStyles = (element: Element, ref: string, win: Window = window): ElementStyles => {
  const computed = win.getComputedStyle(element);
  const styles: Record<string, string> = {};
  for (const property of STYLE_PROPERTIES) {
    const value = computed.getPropertyValue(property).trim();
    if (value && !value.toLowerCase().includes('url(')) styles[property] = cut(value, MAX_STYLE_VALUE_CHARS);
  }
  const rect = element.getBoundingClientRect();
  return {
    ref,
    tag: element.localName,
    box: { x: round(rect.x), y: round(rect.y), width: round(rect.width), height: round(rect.height) },
    styles,
  };
};

export interface IntrospectionSession {
  /** Answers one request; never throws. */
  answer: (request: IntrospectionRequest) => IntrospectionAnswer;
}

/** One chat session's introspection state: the latest snapshot's refs. */
export const createIntrospectionSession = (doc: Document = document, win: Window = window): IntrospectionSession => {
  let refs = new Map<string, Element>();
  return {
    answer(request) {
      try {
        if (request.kind === 'snapshot') {
          const result = serializeSnapshot(doc, win);
          refs = result.refs;
          return { ok: true, result: result.snapshot };
        }
        const ref = request.params.ref ?? '';
        const element = refs.get(ref);
        if (!element) return { ok: false, error: `unknown ref ${ref}; refs are valid only until the next snapshot` };
        if (!element.isConnected) return { ok: false, error: `${ref} is no longer on the page; take a new snapshot` };
        return { ok: true, result: elementStyles(element, ref, win) };
      } catch (error) {
        return { ok: false, error: error instanceof Error ? error.message : String(error) };
      }
    },
  };
};
