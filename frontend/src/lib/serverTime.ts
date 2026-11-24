/**
 * Server timestamps (JAN-394).
 *
 * The backend stores every timestamp as naive UTC and serializes it without an
 * offset (`2026-11-10T09:00:00`). Per the ECMAScript spec, `new Date()` reads an
 * offset-less date-time string as *local* time, which would shift every server
 * time by the viewer's UTC offset. Parse every server-provided timestamp through
 * `parseServerDate` and render the result in the viewer's locale and time zone.
 */

/** A trailing `Z`/`z` or a `±HH:MM` / `±HHMM` offset. */
const EXPLICIT_OFFSET = /(?:[zZ]|[+-]\d{2}:?\d{2})$/;
/** An ISO-8601 date-time without an offset, with `T` or a space between date and time. */
const OFFSETLESS_DATE_TIME = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)$/;
/** A calendar date without a time. */
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

const validOrNull = (date: Date): Date | null => (Number.isNaN(date.getTime()) ? null : date);

/**
 * Turn a server-provided timestamp into a `Date`, or `null` when it is missing or unparseable.
 *
 * - An ISO date-time without an offset is UTC (the backend's naive UTC), so `Z` is appended.
 * - A string with `Z`, `z`, `±HH:MM` or `±HHMM` keeps its own offset.
 * - A date-only `YYYY-MM-DD` is a calendar date, not an instant: it parses as *local*
 *   midnight so the rendered day never moves (UTC midnight would show the previous day
 *   west of Greenwich).
 * - Numbers (epoch milliseconds) and `Date` objects pass through.
 */
export const parseServerDate = (value: string | number | Date | null | undefined): Date | null => {
  if (value === null || value === undefined) return null;
  if (value instanceof Date) return validOrNull(value);
  if (typeof value === 'number') return validOrNull(new Date(value));
  const text = value.trim();
  if (!text) return null;
  const dateOnly = DATE_ONLY.exec(text);
  if (dateOnly) {
    const [year, month, day] = [Number(dateOnly[1]), Number(dateOnly[2]), Number(dateOnly[3])];
    const date = new Date(year, month - 1, day);
    // Reject overflowing dates such as 2026-02-31 instead of rolling them into March.
    return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day ? date : null;
  }
  if (EXPLICIT_OFFSET.test(text)) return validOrNull(new Date(text));
  const offsetless = OFFSETLESS_DATE_TIME.exec(text);
  if (offsetless) return validOrNull(new Date(`${offsetless[1]}T${offsetless[2]}Z`));
  return validOrNull(new Date(text));
};

/** A server timestamp as local date and time (`toLocaleString`), or `null` when missing or unparseable. */
export const formatServerDateTime = (
  value: string | number | Date | null | undefined,
  options?: Intl.DateTimeFormatOptions
): string | null => parseServerDate(value)?.toLocaleString(undefined, options) ?? null;

/** A server timestamp as a local calendar date (`toLocaleDateString`), or `null` when missing or unparseable. */
export const formatServerDate = (
  value: string | number | Date | null | undefined,
  options?: Intl.DateTimeFormatOptions
): string | null => parseServerDate(value)?.toLocaleDateString(undefined, options) ?? null;

/** A server timestamp as a local clock time (`toLocaleTimeString`), or `null` when missing or unparseable. */
export const formatServerTime = (
  value: string | number | Date | null | undefined,
  options?: Intl.DateTimeFormatOptions
): string | null => parseServerDate(value)?.toLocaleTimeString(undefined, options) ?? null;

/**
 * A server timestamp as a `<input type="datetime-local">` value (`YYYY-MM-DDTHH:MM` in local
 * time), or `''`. Sending the input back with `new Date(input).toISOString()` returns UTC, so
 * the round trip does not shift.
 */
export const toDateTimeLocalInputValue = (value: string | number | Date | null | undefined): string => {
  const date = parseServerDate(value);
  if (!date) return '';
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
};
