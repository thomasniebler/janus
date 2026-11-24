import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import {
  formatServerDate,
  formatServerDateTime,
  formatServerTime,
  parseServerDate,
  toDateTimeLocalInputValue,
} from './serverTime';

// Pin a zone with a non-zero UTC offset so reading an offset-less value as local time
// (the JAN-394 bug) would fail here even when the suite itself runs in UTC.
const originalTimeZone = process.env.TZ;
beforeAll(() => {
  process.env.TZ = 'America/New_York';
});
afterAll(() => {
  if (originalTimeZone === undefined) delete process.env.TZ;
  else process.env.TZ = originalTimeZone;
});

describe('parseServerDate', () => {
  it('reads an offset-less date-time as UTC, not local time', () => {
    expect(parseServerDate('2026-11-10T09:00:00')?.toISOString()).toBe('2026-11-10T09:00:00.000Z');
    expect(parseServerDate('2026-11-10T09:00')?.toISOString()).toBe('2026-11-10T09:00:00.000Z');
    expect(parseServerDate('2026-11-10 09:00:00')?.toISOString()).toBe('2026-11-10T09:00:00.000Z');
    expect(parseServerDate('2026-11-10T09:00:00.123456')?.toISOString()).toBe('2026-11-10T09:00:00.123Z');
    // New York is UTC-5 in November: the local clock shows 04:00.
    expect(parseServerDate('2026-11-10T09:00:00')?.getHours()).toBe(4);
  });

  it('keeps an explicit Z or numeric offset', () => {
    expect(parseServerDate('2026-11-10T09:00:00Z')?.toISOString()).toBe('2026-11-10T09:00:00.000Z');
    expect(parseServerDate('2026-11-10T09:00:00z')?.toISOString()).toBe('2026-11-10T09:00:00.000Z');
    expect(parseServerDate('2026-11-10T09:00:00+00:00')?.toISOString()).toBe('2026-11-10T09:00:00.000Z');
    expect(parseServerDate('2026-11-10T09:00:00+02:00')?.toISOString()).toBe('2026-11-10T07:00:00.000Z');
    expect(parseServerDate('2026-11-10T09:00:00-0130')?.toISOString()).toBe('2026-11-10T10:30:00.000Z');
  });

  it('keeps a date-only value on its calendar day (local midnight)', () => {
    const date = parseServerDate('2026-11-10');
    expect(date).not.toBeNull();
    expect([date?.getFullYear(), date?.getMonth(), date?.getDate(), date?.getHours()]).toEqual([2026, 10, 10, 0]);
    expect(parseServerDate('2026-02-31')).toBeNull();
  });

  it('passes numbers and Date objects through', () => {
    const now = new Date('2026-11-10T09:00:00Z');
    expect(parseServerDate(now)).toBe(now);
    expect(parseServerDate(now.getTime())?.toISOString()).toBe(now.toISOString());
  });

  it('returns null for missing or invalid values', () => {
    expect(parseServerDate(null)).toBeNull();
    expect(parseServerDate(undefined)).toBeNull();
    expect(parseServerDate('')).toBeNull();
    expect(parseServerDate('not-a-date')).toBeNull();
    expect(parseServerDate(Number.NaN)).toBeNull();
    expect(parseServerDate(new Date('nope'))).toBeNull();
  });
});

describe('server time formatting', () => {
  it('renders an offset-less value as the UTC instant in local time', () => {
    const utc = new Date('2026-11-10T09:00:00Z');
    expect(formatServerDateTime('2026-11-10T09:00:00')).toBe(utc.toLocaleString());
    expect(formatServerTime('2026-11-10T09:00:00', { hour: '2-digit', minute: '2-digit' })).toBe(
      utc.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    );
    expect(formatServerDate('2026-11-10T02:00:00')).toBe(new Date('2026-11-10T02:00:00Z').toLocaleDateString());
    expect(formatServerDate('2026-11-10')).toBe(new Date(2026, 10, 10).toLocaleDateString());
    expect(formatServerDateTime('garbage')).toBeNull();
  });

  it('prefills a datetime-local input in local time and round-trips back to the same UTC instant', () => {
    const inputValue = toDateTimeLocalInputValue('2026-11-10T09:00:00');
    expect(inputValue).toBe('2026-11-10T04:00');
    expect(new Date(inputValue).toISOString()).toBe('2026-11-10T09:00:00.000Z');
    expect(toDateTimeLocalInputValue('2026-11-10T09:00:00Z')).toBe('2026-11-10T04:00');
    expect(toDateTimeLocalInputValue(null)).toBe('');
    expect(toDateTimeLocalInputValue('not-a-date')).toBe('');
  });
});
