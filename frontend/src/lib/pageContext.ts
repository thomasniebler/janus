/**
 * The page snapshot sent with every chat message (JAN-250).
 *
 * Pages publish what is on screen through `usePageContext` / `usePageContextSection`;
 * `ChatSessionProvider` reads it at send time. Items are ids plus short labels,
 * never bodies or message text, and the whole snapshot is held to
 * `PAGE_CONTEXT_MAX_BYTES` here and again on the server.
 */

export interface PageContextItem {
  type: string;
  id: string | number;
  label: string;
}

export type PageContextFilterValue = string | number | boolean | null;

export interface PageContextSnapshot {
  /** Human page name, e.g. `Emails` or `Memory › Topics`. */
  page: string;
  /** Path plus query string. */
  route: string;
  title?: string;
  selection?: PageContextItem | null;
  filters?: Record<string, PageContextFilterValue>;
  visible_items?: PageContextItem[];
  /** How many visible items the budget dropped. */
  omitted_items?: number;
}

/** What a page publishes; `route` is filled in by the hook. */
export type PageSnapshotInput = Omit<PageContextSnapshot, 'route' | 'omitted_items'>;

/** What a nested section contributes to its page's snapshot. */
export type PageSectionInput = Pick<PageContextSnapshot, 'selection' | 'filters' | 'visible_items'>;

/** Hard budget for the snapshot's UTF-8 JSON, matching the backend's `MAX_PAGE_CONTEXT_BYTES`. */
export const PAGE_CONTEXT_MAX_BYTES = 2048;

const LIMITS = {
  page: 60,
  title: 120,
  route: 200,
  type: 32,
  id: 64,
  label: 80,
  filterKey: 40,
  filterValue: 80,
  filters: 12,
  items: 40,
} as const;

const clip = (value: string, limit: number) => {
  const text = value.replace(/\s+/g, ' ').trim();
  return text.length <= limit ? text : `${text.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
};

/** Builds one `{type, id, label}` item with a short, single-line label. */
export const pageItem = (type: string, id: string | number, label: string): PageContextItem => ({
  type,
  id,
  label: clip(label, LIMITS.label),
});

const cleanItem = (item: PageContextItem): PageContextItem => ({
  type: clip(String(item.type), LIMITS.type),
  id: typeof item.id === 'number' ? item.id : clip(String(item.id), LIMITS.id),
  label: clip(String(item.label), LIMITS.label),
});

const encodedSize = (snapshot: PageContextSnapshot) => new TextEncoder().encode(JSON.stringify(snapshot)).length;

/**
 * Keeps only the snapshot's own keys, cuts strings, and drops visible items
 * (from the end, counted in `omitted_items`), then filters, selection, and
 * title until the JSON fits `maxBytes`.
 */
export const fitPageContext = (
  snapshot: PageContextSnapshot,
  maxBytes: number = PAGE_CONTEXT_MAX_BYTES
): PageContextSnapshot => {
  const fitted: PageContextSnapshot = {
    page: clip(snapshot.page, LIMITS.page),
    route: clip(snapshot.route, LIMITS.route),
  };
  if (snapshot.title) fitted.title = clip(snapshot.title, LIMITS.title);
  if (snapshot.selection) fitted.selection = cleanItem(snapshot.selection);
  const filterEntries = Object.entries(snapshot.filters ?? {}).slice(0, LIMITS.filters);
  if (filterEntries.length > 0) {
    fitted.filters = Object.fromEntries(
      filterEntries.map(([key, value]) => [
        clip(key, LIMITS.filterKey),
        typeof value === 'string' ? clip(value, LIMITS.filterValue) : value,
      ])
    );
  }
  const allItems = snapshot.visible_items ?? [];
  const items = allItems.slice(0, LIMITS.items).map(cleanItem);
  let omitted = (snapshot.omitted_items ?? 0) + (allItems.length - items.length);
  if (items.length > 0) fitted.visible_items = items;
  if (omitted > 0) fitted.omitted_items = omitted;

  while (encodedSize(fitted) > maxBytes && items.length > 0) {
    items.pop();
    omitted += 1;
    fitted.omitted_items = omitted;
    if (items.length === 0) delete fitted.visible_items;
  }
  for (const key of ['filters', 'selection', 'title'] as const) {
    if (encodedSize(fitted) <= maxBytes) break;
    delete fitted[key];
  }
  return fitted;
};

const PAGE_NAMES: Array<[string, string]> = [
  ['/chat', 'Chat'],
  ['/briefing', 'Morning briefing'],
  ['/memory', 'Memory'],
  ['/tasks', 'Agent tasks'],
  ['/emails', 'Emails'],
  ['/settings', 'Settings'],
  ['/docs', 'Documentation'],
];

/** The human page name for a path, used when no page published a snapshot. */
export const routePageName = (pathname: string): string => {
  if (pathname === '/' || pathname === '') return 'Dashboard';
  const match = PAGE_NAMES.find(([prefix]) => pathname === prefix || pathname.startsWith(`${prefix}/`));
  return match ? match[1] : 'Janus';
};

/** The route-only snapshot every message carries at least (JAN-250 step 1). */
export const routePageContext = (pathname: string, search: string): PageContextSnapshot => ({
  page: routePageName(pathname),
  route: `${pathname}${search}`,
});
