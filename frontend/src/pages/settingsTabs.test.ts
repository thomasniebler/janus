import { describe, expect, it } from 'vitest';
import { LEGACY_TABS, PAGE_IDS, SETTINGS_SUB_TABS, locationFor, resolveSettingsLocation } from './settingsTabs';

describe('resolveSettingsLocation', () => {
  it('opens the first sub-tab of a page that has sub-tabs', () => {
    expect(resolveSettingsLocation('connections', null)).toEqual({ tab: 'connections', sub: 'mail' });
    expect(resolveSettingsLocation('system', null)).toEqual({ tab: 'system', sub: 'models' });
  });

  it('keeps a valid sub-tab and falls back for an unknown one', () => {
    expect(resolveSettingsLocation('connections', 'developer')).toEqual({ tab: 'connections', sub: 'developer' });
    expect(resolveSettingsLocation('connections', 'nope')).toEqual({ tab: 'connections', sub: 'mail' });
  });

  it('gives a page without sub-tabs no sub-tab, even when one is passed', () => {
    expect(resolveSettingsLocation('runtime', 'mail')).toEqual({ tab: 'runtime', sub: null });
  });

  it('returns null for a missing or unknown tab', () => {
    expect(resolveSettingsLocation(null, null)).toBeNull();
    expect(resolveSettingsLocation('nope', null)).toBeNull();
  });

  it.each(Object.entries(LEGACY_TABS))('maps the old tab %s onto a real page and sub-tab', (legacy, target) => {
    const resolved = resolveSettingsLocation(legacy, null);
    expect(resolved).toEqual({ tab: target.tab, sub: target.sub });
    expect(PAGE_IDS).toContain(target.tab);
    expect(SETTINGS_SUB_TABS[target.tab]?.map((sub) => sub.id)).toContain(target.sub);
  });

  it('never lets a legacy id shadow a current page', () => {
    for (const legacy of Object.keys(LEGACY_TABS)) expect(PAGE_IDS).not.toContain(legacy);
  });
});

describe('locationFor', () => {
  it('defaults to the first sub-tab', () => {
    expect(locationFor('connections')).toEqual({ tab: 'connections', sub: 'mail' });
  });
});
