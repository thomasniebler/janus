import { UI_VERSION_HTML_CLASSES, UI_VERSIONS, type UIVersion } from '../contexts/uiVersion';

/**
 * The DOM serializer behind `get_ui_snapshot` (JAN-285): a bounded semantic
 * outline of the page the user has open (landmarks, headings, buttons, links,
 * form controls with their labels, images' alt text, short visible text runs),
 * each node with a `ref` valid until the next snapshot, plus the viewport.
 *
 * It only reads, and never a form control's value: not `.value`, `checked`,
 * nor a textarea's or select's text. Password, one-time-code and payment-card
 * inputs are left out entirely, `contenteditable` content is skipped, and so
 * are the chat panel (`[data-janus-chat]`) and every `[data-janus-private]`
 * subtree. The backend re-applies every cap (`services/ui_introspection.py`).
 * The protocol and the styles side live in `uiIntrospection.ts`.
 */

export const MAX_SNAPSHOT_NODES = 200;
export const MAX_SNAPSHOT_BYTES = 24 * 1024;
const MAX_VISITED_ELEMENTS = 5000;
const MAX_NAME_CHARS = 120;
const MAX_TEXT_CHARS = 160;
const MAX_ROUTE_CHARS = 200;
const MAX_TITLE_CHARS = 120;
const MAX_DEPTH = 20;

/** Marker every chat panel carries so Janus never reads its own transcript back. */
const CHAT_PANEL_ATTRIBUTE = 'data-janus-chat';
/** Marker a page puts on anything Janus must never read. */
const PRIVATE_ATTRIBUTE = 'data-janus-private';

export interface SnapshotNode {
  ref: string;
  role: string;
  depth: number;
  name?: string;
  text?: string;
  level?: number;
  input_type?: string;
  href?: string;
  disabled?: true;
  required?: true;
  invalid?: true;
  offscreen?: true;
}

interface UIViewport {
  width: number;
  height: number;
  scroll_x: number;
  scroll_y: number;
  device_pixel_ratio: number;
  color_scheme: 'light' | 'dark';
  ui_shell: UIVersion;
}

export interface UISnapshot {
  route: string;
  title: string;
  viewport: UIViewport;
  nodes: SnapshotNode[];
  omitted_nodes?: number;
  skipped_private?: number;
}

type NodeDraft = Omit<SnapshotNode, 'ref'>;

const LANDMARK_ROLES = new Set([
  'banner', 'navigation', 'main', 'complementary', 'contentinfo', 'region', 'form', 'search', 'dialog', 'alertdialog',
]);
const CONTROL_ROLES = new Set([
  'textbox', 'searchbox', 'checkbox', 'radio', 'combobox', 'listbox', 'switch', 'slider', 'spinbutton',
]);
/** Roles taken from an explicit `role` attribute. */
const EXPLICIT_ROLES = new Set([
  ...LANDMARK_ROLES, ...CONTROL_ROLES, 'alert', 'status', 'heading', 'button', 'link', 'tab', 'tablist', 'img',
]);
/** Roles whose subtree is summarized by the node's name instead of being walked. */
const LEAF_ROLES = new Set([...CONTROL_ROLES, 'alert', 'status', 'heading', 'button', 'link', 'tab', 'img']);

const SKIPPED_TAGS = new Set([
  'script', 'style', 'noscript', 'template', 'svg', 'canvas', 'iframe', 'object', 'embed', 'head', 'link', 'meta',
  'option', 'optgroup',
]);
const CONTROL_TAGS = new Set(['input', 'select', 'textarea']);
const SECRET_AUTOCOMPLETE_TOKENS = new Set(['current-password', 'new-password', 'one-time-code']);
const BUTTON_INPUT_TYPES = new Set(['button', 'submit', 'reset', 'image', 'file']);

export const cut = (value: string, limit: number): string => {
  const text = value.replace(/\s+/g, ' ').trim();
  return text.length <= limit ? text : `${text.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
};

const attr = (element: Element, name: string): string => element.getAttribute(name) ?? '';

const isEditable = (element: Element): boolean => {
  const value = element.getAttribute('contenteditable');
  return value !== null && value !== 'false';
};

/** A form control or editable region: its content is the user's input, never read. */
const holdsInput = (element: Element): boolean => CONTROL_TAGS.has(element.localName) || isEditable(element);

/**
 * Whether any whitespace-separated `autocomplete` token names a secret, so prefixed values
 * like `billing cc-number` or `section-checkout one-time-code` count too.
 */
const hasSecretAutocomplete = (element: Element): boolean =>
  attr(element, 'autocomplete')
    .toLowerCase()
    .split(/\s+/)
    .some((token) => SECRET_AUTOCOMPLETE_TOKENS.has(token) || token.startsWith('cc-'));

/** A password, one-time-code, or payment-card input: never listed at all. */
const isSecretInput = (element: Element): boolean =>
  element.localName === 'input' && (attr(element, 'type').toLowerCase() === 'password' || hasSecretAutocomplete(element));

const isHidden = (element: Element, win: Window): boolean => {
  if (element.hasAttribute('hidden') || attr(element, 'aria-hidden') === 'true') return true;
  if (element.localName === 'input' && attr(element, 'type').toLowerCase() === 'hidden') return true;
  const style = win.getComputedStyle(element);
  return style.display === 'none' || style.visibility === 'hidden';
};

/** Never descended into: not content, private, the chat panel, or aria-hidden. */
const isOpaque = (element: Element): boolean =>
  SKIPPED_TAGS.has(element.localName) ||
  element.hasAttribute(PRIVATE_ATTRIBUTE) ||
  element.hasAttribute(CHAT_PANEL_ATTRIBUTE) ||
  attr(element, 'aria-hidden') === 'true';

/** Whether the walker would leave `element` out as hidden; a detached document counts as visible. */
const isHiddenIn = (element: Element): boolean => {
  const win = element.ownerDocument.defaultView;
  return win ? isHidden(element, win) : element.hasAttribute('hidden') || attr(element, 'aria-hidden') === 'true';
};

/**
 * Visible text of a subtree without any form control's content, hidden descendants,
 * private parts, or the chat panel.
 */
const textOf = (root: Element, limit: number): string => {
  if (holdsInput(root)) return '';
  const parts: string[] = [];
  let length = 0;
  const walk = (node: Node) => {
    if (length > limit) return;
    if (node.nodeType === Node.TEXT_NODE) {
      parts.push(node.textContent ?? '');
      length += parts[parts.length - 1].length;
    } else if (node instanceof Element && !holdsInput(node) && !isOpaque(node) && !isHiddenIn(node)) {
      node.childNodes.forEach(walk);
    }
  };
  root.childNodes.forEach(walk);
  return cut(parts.join(' '), limit);
};

const labelledBy = (element: Element, doc: Document): string =>
  cut(
    attr(element, 'aria-labelledby')
      .split(/\s+/)
      .filter(Boolean)
      .map((id) => doc.getElementById(id))
      .filter((label): label is HTMLElement => label !== null && !label.closest(`[${PRIVATE_ATTRIBUTE}]`))
      .map((label) => textOf(label, MAX_NAME_CHARS))
      .join(' '),
    MAX_NAME_CHARS
  );

const controlLabel = (element: Element, doc: Document): string => {
  const id = element.getAttribute('id');
  const forLabel = id ? Array.from(doc.getElementsByTagName('label')).find((label) => label.htmlFor === id) : undefined;
  const label = forLabel ?? element.closest('label');
  const text = label ? textOf(label, MAX_NAME_CHARS) : '';
  return text || cut(attr(element, 'placeholder') || attr(element, 'title'), MAX_NAME_CHARS);
};

/** The alt text of the first image inside `root` that `textOf` would also reach. */
const visibleImageAlt = (root: Element): string => {
  const reachable = (image: Element): boolean => {
    for (let node: Element | null = image; node && node !== root; node = node.parentElement) {
      if (isOpaque(node) || isHiddenIn(node) || (node !== image && holdsInput(node))) return false;
    }
    return true;
  };
  return Array.from(root.querySelectorAll('img[alt]')).find(reachable)?.getAttribute('alt') ?? '';
};

/** What a node's name is made of, by kind; never a control's value. */
const implicitName = (element: Element, role: string, doc: Document): string => {
  if (element.localName === 'img') return cut(attr(element, 'alt'), MAX_NAME_CHARS);
  // A button-type input's `value` attribute is its caption, not something the user typed.
  if (element.localName === 'input' && role === 'button') {
    return cut(attr(element, 'value'), MAX_NAME_CHARS) || controlLabel(element, doc);
  }
  if (CONTROL_ROLES.has(role) || holdsInput(element)) return controlLabel(element, doc);
  if (LANDMARK_ROLES.has(role) || role === 'tablist') return '';
  const text = textOf(element, MAX_NAME_CHARS);
  if (text) return text;
  return cut(visibleImageAlt(element) || attr(element, 'title'), MAX_NAME_CHARS);
};

const nameOf = (element: Element, role: string, doc: Document): string =>
  cut(attr(element, 'aria-label'), MAX_NAME_CHARS) || labelledBy(element, doc) || implicitName(element, role, doc);

const inputRole = (element: Element): string => {
  const type = (element.getAttribute('type') ?? 'text').toLowerCase();
  if (BUTTON_INPUT_TYPES.has(type)) return 'button';
  const roles: Record<string, string> = {
    checkbox: 'checkbox',
    radio: 'radio',
    range: 'slider',
    number: 'spinbutton',
    search: 'searchbox',
  };
  return roles[type] ?? 'textbox';
};

const IMPLICIT_ROLES: Record<string, (element: Element) => string | null> = {
  header: () => 'banner',
  nav: () => 'navigation',
  main: () => 'main',
  aside: () => 'complementary',
  footer: () => 'contentinfo',
  section: (element) =>
    element.hasAttribute('aria-label') || element.hasAttribute('aria-labelledby') ? 'region' : null,
  form: () => 'form',
  search: () => 'search',
  dialog: () => 'dialog',
  button: () => 'button',
  summary: () => 'button',
  a: (element) => (element.hasAttribute('href') ? 'link' : null),
  textarea: () => 'textbox',
  select: (element) => (element.hasAttribute('multiple') ? 'listbox' : 'combobox'),
  img: () => 'img',
  input: inputRole,
};

const roleOf = (element: Element): string | null => {
  const explicit = attr(element, 'role').trim().split(/\s+/)[0];
  if (explicit === 'presentation' || explicit === 'none') return null;
  if (explicit && EXPLICIT_ROLES.has(explicit)) return explicit;
  if (/^h[1-6]$/.test(element.localName)) return 'heading';
  if (isEditable(element)) return 'textbox';
  return IMPLICIT_ROLES[element.localName]?.(element) ?? null;
};

const inputType = (element: Element): string | undefined => {
  if (element.localName === 'input') return (element.getAttribute('type') ?? 'text').toLowerCase();
  if (element.localName === 'textarea' || element.localName === 'select') return element.localName;
  return isEditable(element) ? 'contenteditable' : undefined;
};

const linkTarget = (element: Element, win: Window): string | undefined => {
  const raw = element.getAttribute('href');
  if (!raw || raw.startsWith('#')) return undefined;
  try {
    const url = new URL(raw, win.location.href);
    if (url.origin === win.location.origin) return cut(url.pathname, MAX_ROUTE_CHARS);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.origin : undefined;
  } catch {
    return undefined;
  }
};

const isDisabled = (element: Element): boolean => {
  if (attr(element, 'aria-disabled') === 'true') return true;
  try {
    return element.matches(':disabled');
  } catch {
    return false;
  }
};

const isOffscreen = (element: Element, win: Window): boolean => {
  const rect = element.getBoundingClientRect();
  return rect.bottom < 0 || rect.right < 0 || rect.top > win.innerHeight || rect.left > win.innerWidth;
};

const headingLevel = (element: Element): number => {
  const tagLevel = /^h([1-6])$/.exec(element.localName)?.[1];
  if (tagLevel) return Number(tagLevel);
  const ariaLevel = Number(element.getAttribute('aria-level'));
  return ariaLevel >= 1 && ariaLevel <= 6 ? ariaLevel : 2;
};

/** Labels, roles and states of a control or button: never its value. */
const addControlState = (node: NodeDraft, element: Element, role: string) => {
  const type = CONTROL_ROLES.has(role) ? inputType(element) : undefined;
  if (type) node.input_type = type;
  if (isDisabled(element)) node.disabled = true;
  if (element.hasAttribute('required') || attr(element, 'aria-required') === 'true') node.required = true;
  if (attr(element, 'aria-invalid') === 'true') node.invalid = true;
};

const describe = (element: Element, role: string, depth: number, doc: Document, win: Window): NodeDraft => {
  const node: NodeDraft = { role, depth };
  const name = nameOf(element, role, doc);
  if (name) node.name = name;
  if (role === 'heading') node.level = headingLevel(element);
  if (role === 'link') {
    const href = linkTarget(element, win);
    if (href) node.href = href;
  }
  if (CONTROL_ROLES.has(role) || role === 'button') addControlState(node, element, role);
  if (isOffscreen(element, win)) node.offscreen = true;
  return node;
};

/** The element's own text (not its children's), unless it is a control's label (already its name). */
const textRun = (element: Element): string => {
  if (element.localName === 'label' && (element.hasAttribute('for') || element.querySelector('input, select, textarea'))) {
    return '';
  }
  return cut(
    Array.from(element.childNodes)
      .filter((node) => node.nodeType === Node.TEXT_NODE)
      .map((node) => node.textContent ?? '')
      .join(' '),
    MAX_TEXT_CHARS
  );
};

const encodedSize = (value: unknown): number => new TextEncoder().encode(JSON.stringify(value)).length;

/** Drops nodes from the end (counted in `omitted_nodes`) until the snapshot's JSON fits `maxBytes`. */
export const fitSnapshot = (snapshot: UISnapshot, maxBytes = MAX_SNAPSHOT_BYTES): UISnapshot => {
  if (encodedSize(snapshot) <= maxBytes) return snapshot;
  const omitted = snapshot.omitted_nodes ?? 0;
  const total = snapshot.nodes.length;
  const withCount = (count: number): UISnapshot => ({
    ...snapshot,
    nodes: snapshot.nodes.slice(0, count),
    omitted_nodes: omitted + total - count,
  });
  let low = 0;
  let high = total;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (encodedSize(withCount(middle)) <= maxBytes) low = middle;
    else high = middle - 1;
  }
  return withCount(low);
};

const viewportOf = (doc: Document, win: Window): UIViewport => {
  const classes = doc.documentElement.classList;
  const shell = UI_VERSIONS.find((version) => {
    const htmlClass = UI_VERSION_HTML_CLASSES[version];
    return htmlClass !== null && classes.contains(htmlClass);
  });
  return {
    width: Math.round(win.innerWidth),
    height: Math.round(win.innerHeight),
    scroll_x: Math.round(win.scrollX),
    scroll_y: Math.round(win.scrollY),
    device_pixel_ratio: win.devicePixelRatio || 1,
    color_scheme: classes.contains('dark') ? 'dark' : 'light',
    ui_shell: shell ?? 'v1',
  };
};

export interface SnapshotResult {
  snapshot: UISnapshot;
  /** `ref` -> element for the nodes the snapshot kept; valid until the next snapshot. */
  refs: Map<string, Element>;
}

/** Walks the page depth-first and collects the outline's nodes and their elements. */
class OutlineWalker {
  nodes: SnapshotNode[] = [];
  elements: Element[] = [];
  omitted = 0;
  skippedPrivate = 0;
  private visited = 0;
  private readonly doc: Document;
  private readonly win: Window;

  constructor(doc: Document, win: Window) {
    this.doc = doc;
    this.win = win;
  }

  private emit(node: NodeDraft, element: Element) {
    if (this.nodes.length >= MAX_SNAPSHOT_NODES) {
      this.omitted += 1;
      return;
    }
    this.nodes.push({ ref: `e${this.nodes.length + 1}`, ...node });
    this.elements.push(element);
  }

  /** Whether to skip `element` and its subtree. */
  private skips(element: Element): boolean {
    this.visited += 1;
    if (this.visited > MAX_VISITED_ELEMENTS) return true;
    if (element.hasAttribute(PRIVATE_ATTRIBUTE)) {
      this.skippedPrivate += 1;
      return true;
    }
    return isOpaque(element) || isSecretInput(element) || isHidden(element, this.win);
  }

  walk(element: Element, depth: number) {
    if (this.skips(element)) return;
    const role = roleOf(element);
    const level = Math.min(depth, MAX_DEPTH);
    if (role) {
      this.emit(describe(element, role, level, this.doc, this.win), element);
      if (LEAF_ROLES.has(role) || holdsInput(element)) return;
    } else {
      const text = textRun(element);
      if (text) {
        const node: NodeDraft = { role: 'text', depth: level, text };
        if (isOffscreen(element, this.win)) node.offscreen = true;
        this.emit(node, element);
      }
    }
    const childDepth = role && LANDMARK_ROLES.has(role) ? depth + 1 : depth;
    for (const child of Array.from(element.children)) this.walk(child, childDepth);
  }
}

/** Serializes the visible page into a bounded semantic outline. */
export const serializeSnapshot = (doc: Document = document, win: Window = window): SnapshotResult => {
  const walker = new OutlineWalker(doc, win);
  if (doc.body) walker.walk(doc.body, 0);
  const snapshot: UISnapshot = {
    route: cut(`${win.location.pathname}${win.location.search}`, MAX_ROUTE_CHARS),
    title: cut(doc.title, MAX_TITLE_CHARS),
    viewport: viewportOf(doc, win),
    nodes: walker.nodes,
  };
  if (walker.omitted) snapshot.omitted_nodes = walker.omitted;
  if (walker.skippedPrivate) snapshot.skipped_private = walker.skippedPrivate;
  const fitted = fitSnapshot(snapshot);
  const refs = new Map<string, Element>();
  fitted.nodes.forEach((node, index) => refs.set(node.ref, walker.elements[index]));
  return { snapshot: fitted, refs };
};
