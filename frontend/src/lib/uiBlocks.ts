import { z } from 'zod';

/**
 * The chat UI-block catalog (JAN-252), mirrored from `src/janus/services/ui_blocks.py`.
 * Every block that reaches the transcript (a `__UI_BLOCK__` frame or a stored
 * `ui_blocks` row) is parsed here again before it renders; one that does not parse
 * is shown as JSON instead. Keep the limits in sync with the backend.
 */

/** Prefix of the `/ws/chat` UI-block frame. */
export const UI_BLOCK_PREFIX = '__UI_BLOCK__:';
export const UI_BLOCK_SCHEMA_VERSION = 1;
export const UI_BLOCK_MAX_BYTES = 8192;

export const UI_BLOCK_LIMITS = {
  tableColumns: 8,
  tableRows: 50,
  chartPoints: 50,
  chartSeries: 5,
  checklistItems: 50,
  keyValueItems: 30,
  actionButtons: 6,
  cardFields: 12,
  cardLinks: 4,
} as const;

/** The only image source a block may name: an authenticated, same-origin Janus image (JAN-253). */
export const IMAGE_URL_PATTERN = /^\/api\/images\/[A-Za-z0-9_-]{1,64}$/;

// eslint-disable-next-line no-control-regex
const UNSAFE_HREF_CHARS = /[\s\u0000-\u001f\\]/;

/** A same-origin path (`/...`, not `//`) or an `https://` URL with a host. */
export const isAllowedHref = (href: string): boolean => {
  if (!href || UNSAFE_HREF_CHARS.test(href)) return false;
  if (href.startsWith('/')) return !href.startsWith('//');
  try {
    const url = new URL(href);
    return url.protocol === 'https:' && url.hostname !== '';
  } catch {
    return false;
  }
};

const text = (max: number) => z.string().max(max);
const required = (max: number) => z.string().min(1).max(max);
const finite = z.number().refine(Number.isFinite, 'must be a finite number');
const cell = z.union([text(300), finite, z.boolean(), z.null()]);

const link = z.strictObject({
  label: required(80),
  href: z.string().min(1).max(500).refine(isAllowedHref, 'href must be a same-origin path or an https:// URL'),
});

const base = {
  id: z.string().min(1).max(64),
  version: z.literal(UI_BLOCK_SCHEMA_VERSION),
  title: text(120).optional(),
};

const cardBlock = z.strictObject({
  ...base,
  type: z.literal('card'),
  title: required(120),
  subtitle: text(200).optional(),
  body: text(2000).optional(),
  fields: z
    .array(z.strictObject({ label: required(60), value: text(300) }))
    .max(UI_BLOCK_LIMITS.cardFields)
    .optional(),
  links: z.array(link).max(UI_BLOCK_LIMITS.cardLinks).optional(),
});

const tableBlock = z
  .strictObject({
    ...base,
    type: z.literal('table'),
    columns: z.array(required(60)).min(1).max(UI_BLOCK_LIMITS.tableColumns),
    rows: z.array(z.array(cell)).max(UI_BLOCK_LIMITS.tableRows),
  })
  .refine((block) => block.rows.every((row) => row.length === block.columns.length), 'every row needs one cell per column');

const chartBlock = z
  .strictObject({
    ...base,
    type: z.literal('chart'),
    kind: z.enum(['bar', 'line', 'pie']),
    labels: z.array(text(40)).min(1).max(UI_BLOCK_LIMITS.chartPoints),
    series: z
      .array(z.strictObject({ name: required(60), values: z.array(finite).min(1).max(UI_BLOCK_LIMITS.chartPoints) }))
      .min(1)
      .max(UI_BLOCK_LIMITS.chartSeries),
    unit: text(16).optional(),
  })
  .refine((block) => block.series.every((series) => series.values.length === block.labels.length), 'one value per label')
  .refine((block) => {
    if (block.kind !== 'pie') return true;
    const values = block.series[0]?.values ?? [];
    return block.series.length === 1 && values.every((value) => value >= 0) && values.reduce((a, b) => a + b, 0) > 0;
  }, 'a pie chart has one series of non-negative values with a positive sum');

const checklistBlock = z.strictObject({
  ...base,
  type: z.literal('checklist'),
  items: z
    .array(z.strictObject({ text: required(300), checked: z.boolean() }))
    .min(1)
    .max(UI_BLOCK_LIMITS.checklistItems),
});

const keyValueBlock = z.strictObject({
  ...base,
  type: z.literal('key_value'),
  items: z
    .array(z.strictObject({ key: required(80), value: z.union([text(500), finite, z.boolean()]) }))
    .min(1)
    .max(UI_BLOCK_LIMITS.keyValueItems),
});

const actionsBlock = z.strictObject({
  ...base,
  type: z.literal('actions'),
  buttons: z
    .array(
      z.strictObject({
        label: required(40),
        tool: z.string().regex(/^[A-Za-z0-9_.-]{1,64}$/),
        args: z.record(z.string(), z.unknown()),
        style: z.enum(['primary', 'secondary', 'danger']),
      })
    )
    .min(1)
    .max(UI_BLOCK_LIMITS.actionButtons),
});

const imageBlock = z.strictObject({
  ...base,
  type: z.literal('image'),
  alt: required(300),
  url: z.string().regex(IMAGE_URL_PATTERN).optional(),
  caption: text(300).optional(),
});

const uiBlockSchema = z.discriminatedUnion('type', [
  cardBlock,
  tableBlock,
  chartBlock,
  checklistBlock,
  keyValueBlock,
  actionsBlock,
  imageBlock,
]);

export type UIBlock = z.infer<typeof uiBlockSchema>;
export type CardUIBlock = z.infer<typeof cardBlock>;
export type TableUIBlock = z.infer<typeof tableBlock>;
export type ChartUIBlock = z.infer<typeof chartBlock>;
export type ChecklistUIBlock = z.infer<typeof checklistBlock>;
export type KeyValueUIBlock = z.infer<typeof keyValueBlock>;
export type ActionsUIBlock = z.infer<typeof actionsBlock>;
export type ImageUIBlock = z.infer<typeof imageBlock>;
export type UIBlockAction = ActionsUIBlock['buttons'][number];

const byteLength = (value: unknown): number => {
  try {
    return new TextEncoder().encode(JSON.stringify(value)).length;
  } catch {
    return Number.POSITIVE_INFINITY;
  }
};

/** Validates one raw block (a frame body or a stored row's `payload`); `null` when it cannot render. */
export const parseUIBlock = (raw: unknown): UIBlock | null => {
  if (byteLength(raw) > UI_BLOCK_MAX_BYTES) return null;
  const result = uiBlockSchema.safeParse(raw);
  return result.success ? result.data : null;
};

/** Parses a `__UI_BLOCK__:` frame body to its raw JSON (validated at render time); `undefined` for bad JSON. */
export const parseUIBlockFrame = (body: string): unknown => {
  try {
    return JSON.parse(body) as unknown;
  } catch {
    return undefined;
  }
};

/** The raw blocks of a stored message's `ui_blocks` rows, in order. */
export const toUIBlockPayloads = (rows: unknown): unknown[] =>
  Array.isArray(rows)
    ? rows
        .map((row) => (row && typeof row === 'object' ? (row as { payload?: unknown }).payload : undefined))
        .filter((payload) => payload !== undefined)
    : [];

/** The chat message an action button sends: it names the button, the tool, and its arguments. */
export const actionMessage = (action: UIBlockAction): string =>
  `Run "${action.label}": call ${action.tool}${formatActionArgs(action.args)}`;

/** ` with {...}` for a button's arguments, or `''` when it has none. */
export const formatActionArgs = (args: Record<string, unknown>): string =>
  Object.keys(args).length > 0 ? ` with ${JSON.stringify(args)}` : '';
