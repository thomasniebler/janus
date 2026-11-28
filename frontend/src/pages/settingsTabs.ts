/** The Settings pages, their sub-tabs, and how links from before the regrouping still land somewhere. */

export const PAGE_IDS = [
  'system',
  'connections',
  'tools',
  'character',
  'skills',
  'runtime',
  'briefing',
  'security',
  'appearance',
  'status',
  'stats',
  'events',
  'errors',
] as const;

export type TabId = (typeof PAGE_IDS)[number];

export interface SettingsSubTab {
  id: string;
  name: string;
}

/** Pages with a second level of tabs, in display order. The first entry is the default. */
export const SETTINGS_SUB_TABS: Partial<Record<TabId, readonly SettingsSubTab[]>> = {
  system: [
    { id: 'models', name: 'Models' },
    { id: 'capabilities', name: 'Search & images' },
  ],
  connections: [
    { id: 'mail', name: 'Mail' },
    { id: 'calendar', name: 'Calendar & tasks' },
    { id: 'messaging', name: 'Messaging' },
    { id: 'developer', name: 'Developer' },
  ],
};

/** `?tab=` values that were top-level tabs before the regrouping, mapped to where their content lives now. */
export const LEGACY_TABS: Record<string, { tab: TabId; sub: string }> = {
  accounts: { tab: 'connections', sub: 'mail' },
  'mail-ingestion': { tab: 'connections', sub: 'mail' },
  whatsapp: { tab: 'connections', sub: 'messaging' },
  github: { tab: 'connections', sub: 'developer' },
  linear: { tab: 'connections', sub: 'developer' },
  // `planner` was the Connections tab's name before Google and Microsoft moved here.
  planner: { tab: 'connections', sub: 'calendar' },
};

export const DEFAULT_TAB: TabId = 'system';

export interface SettingsLocation {
  tab: TabId;
  /** The active sub-tab, or `null` for a page without any. */
  sub: string | null;
}

const isPageId = (value: string): value is TabId => (PAGE_IDS as readonly string[]).includes(value);

/** The location of a page: its first sub-tab (or `sub`, when that is one of its sub-tabs). */
export const locationFor = (tab: TabId, sub?: string | null): SettingsLocation => {
  const subTabs = SETTINGS_SUB_TABS[tab];
  if (!subTabs) return { tab, sub: null };
  return { tab, sub: subTabs.find((candidate) => candidate.id === sub)?.id ?? subTabs[0].id };
};

/** Resolves `?tab=` and `?sub=` (current or legacy ids) to a location, or `null` when `tab` is unknown. */
export const resolveSettingsLocation = (tab: string | null, sub: string | null): SettingsLocation | null => {
  if (!tab) return null;
  const legacy = LEGACY_TABS[tab];
  if (legacy) return locationFor(legacy.tab, sub ?? legacy.sub);
  return isPageId(tab) ? locationFor(tab, sub) : null;
};
