import { useId, useState } from 'react';

/** One stacked series; `color` is a CSS color, normally one of the `--viz-*` variables below. */
export interface DailySeries<K extends string> {
  key: K;
  label: string;
  color: string;
}

export interface DailyBarChartProps<K extends string> {
  title: string;
  /** One row per local day, oldest first. */
  data: ({ date: string } & Record<K, number>)[];
  series: DailySeries<K>[];
  /** Accessible summary read instead of the SVG. */
  summary: string;
  height?: number;
}

const WIDTH = 720;
const AXIS_WIDTH = 32;
const GAP = 2;

/**
 * Validated categorical slots and the fixed status palette, stepped for light
 * and dark surfaces (dataviz reference palette). Consumers reference roles via
 * `var(--viz-…)`, so both themes swap in one place.
 */
export const VIZ_STYLE = `
.stats-viz {
  --viz-1: #2a78d6; --viz-2: #eb6834; --viz-3: #1baf7a; --viz-4: #eda100;
  --viz-good: #0ca30c; --viz-warning: #fab219; --viz-critical: #d03b3b; --viz-neutral: #9a9893;
  --viz-surface: #ffffff; --viz-grid: #e7e6e2;
}
.dark .stats-viz {
  --viz-1: #3987e5; --viz-2: #d95926; --viz-3: #199e70; --viz-4: #c98500;
  --viz-neutral: #6f6e69; --viz-surface: #2b2b2b; --viz-grid: #3a3a38;
}
`;

const niceMax = (value: number): number => {
  if (value <= 4) return Math.max(value, 1);
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find((candidate) => candidate * magnitude >= value / 4) ?? 10;
  return Math.ceil(value / (step * magnitude)) * step * magnitude;
};

const shortDate = (isoDate: string): string => {
  const [year, month, day] = isoDate.split('-').map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
};

/**
 * Stacked daily columns with a per-day hover tooltip, a legend, and a table
 * view. Identity is never carried by color alone: the legend names every
 * series, the tooltip lists values by label, and the table has the numbers.
 */
export function DailyBarChart<K extends string>({ title, data, series, summary, height = 180 }: DailyBarChartProps<K>) {
  const [hovered, setHovered] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);
  const titleId = useId();
  const totals = data.map((row) => series.reduce((sum, item) => sum + (row[item.key] ?? 0), 0));
  const max = niceMax(Math.max(0, ...totals));
  const plotWidth = WIDTH - AXIS_WIDTH;
  const slot = plotWidth / Math.max(data.length, 1);
  const barWidth = Math.max(Math.min(slot - GAP, 28), 2);
  const y = (value: number) => (value / max) * (height - 8);
  const labelEvery = Math.ceil(data.length / 8);
  const hoveredRow = hovered !== null ? data[hovered] : null;

  return (
    <figure className="space-y-2" aria-labelledby={titleId} data-testid={`daily-chart-${title}`}>
      <figcaption className="flex flex-wrap items-center justify-between gap-2">
        <span id={titleId} className="text-sm font-semibold">{title}</span>
        <button type="button" className="text-xs font-medium text-primary hover:underline" onClick={() => setShowTable((value) => !value)}>
          {showTable ? 'Show chart' : 'Show table'}
        </button>
      </figcaption>
      {series.length > 1 && (
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600 dark:text-gray-300" aria-label={`${title} legend`}>
          {series.map((item) => (
            <li key={item.key} className="flex items-center gap-1.5">
              <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: item.color }} />
              {item.label}
            </li>
          ))}
        </ul>
      )}
      {showTable ? (
        <div className="max-h-72 overflow-auto rounded-lg border border-gray-200 dark:border-gray-700">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-gray-50 dark:bg-gray-800">
              <tr>
                <th className="px-2 py-1 text-left font-semibold">Day</th>
                {series.map((item) => <th key={item.key} className="px-2 py-1 text-right font-semibold">{item.label}</th>)}
              </tr>
            </thead>
            <tbody>
              {data.map((row) => (
                <tr key={row.date} className="border-t border-gray-100 dark:border-gray-800">
                  <td className="px-2 py-1">{row.date}</td>
                  {series.map((item) => <td key={item.key} className="px-2 py-1 text-right tabular-nums">{row[item.key]}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="relative">
          <svg viewBox={`0 0 ${WIDTH} ${height + 20}`} className="block h-auto w-full" role="img" aria-label={summary}>
            {/* Counts are whole numbers, so a half-way gridline only when it lands on one. */}
            {[0, 0.5, 1].filter((fraction) => Number.isInteger(max * fraction)).map((fraction) => {
              const lineY = height - y(max * fraction);
              return (
                <g key={fraction}>
                  <line x1={AXIS_WIDTH} x2={WIDTH} y1={lineY} y2={lineY} stroke="var(--viz-grid)" strokeWidth={1} />
                  <text x={AXIS_WIDTH - 6} y={lineY + 3} textAnchor="end" className="fill-gray-500 dark:fill-gray-400" fontSize={10}>
                    {max * fraction}
                  </text>
                </g>
              );
            })}
            {data.map((row, index) => {
              const x = AXIS_WIDTH + index * slot + (slot - barWidth) / 2;
              let top = height;
              const segments = series.filter((item) => (row[item.key] ?? 0) > 0);
              return (
                <g key={row.date}>
                  {segments.map((item, segmentIndex) => {
                    const segmentHeight = y(row[item.key]);
                    top -= segmentHeight;
                    const isTop = segmentIndex === segments.length - 1;
                    // A 2px surface gap separates a segment from the one below it; only the top end is rounded.
                    const drawnHeight = Math.max(segmentHeight - (segmentIndex > 0 ? GAP : 0), 1);
                    return (
                      <path
                        key={item.key}
                        d={roundedTopRect(x, top, barWidth, drawnHeight, isTop ? Math.min(4, barWidth / 2, drawnHeight) : 0)}
                        fill={item.color}
                        opacity={hovered === null || hovered === index ? 1 : 0.45}
                      />
                    );
                  })}
                  {index % labelEvery === 0 && (
                    <text x={x + barWidth / 2} y={height + 14} textAnchor="middle" fontSize={10} className="fill-gray-500 dark:fill-gray-400">
                      {shortDate(row.date)}
                    </text>
                  )}
                  {/* The hit target is the whole day column, larger than the mark. */}
                  <rect
                    x={AXIS_WIDTH + index * slot}
                    y={0}
                    width={slot}
                    height={height}
                    fill="transparent"
                    data-testid="daily-chart-hit"
                    onMouseEnter={() => setHovered(index)}
                    onMouseLeave={() => setHovered(null)}
                  />
                </g>
              );
            })}
            <line x1={AXIS_WIDTH} x2={WIDTH} y1={height} y2={height} stroke="var(--viz-grid)" strokeWidth={1} />
          </svg>
          {hoveredRow && hovered !== null && (
            <div
              role="tooltip"
              className="pointer-events-none absolute top-0 z-10 min-w-[9rem] rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs shadow-lg dark:border-gray-700 dark:bg-gray-900"
              style={{
                left: `${((AXIS_WIDTH + (hovered + 0.5) * slot) / WIDTH) * 100}%`,
                transform: hovered > data.length / 2 ? 'translateX(calc(-100% - 8px))' : 'translateX(8px)',
              }}
            >
              <p className="font-semibold">{shortDate(hoveredRow.date)}</p>
              {series.map((item) => (
                <p key={item.key} className="flex items-center justify-between gap-3">
                  <span className="flex items-center gap-1.5">
                    <span aria-hidden className="inline-block h-2 w-2 rounded-sm" style={{ background: item.color }} />
                    {item.label}
                  </span>
                  <span className="tabular-nums">{hoveredRow[item.key]}</span>
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </figure>
  );
}

/** A bar path with only its two top corners rounded, anchored flat to the baseline. */
const roundedTopRect = (x: number, y: number, width: number, height: number, radius: number): string => {
  if (radius <= 0) return `M${x},${y}h${width}v${height}h${-width}Z`;
  return (
    `M${x},${y + height}V${y + radius}Q${x},${y} ${x + radius},${y}` +
    `H${x + width - radius}Q${x + width},${y} ${x + width},${y + radius}V${y + height}Z`
  );
};
