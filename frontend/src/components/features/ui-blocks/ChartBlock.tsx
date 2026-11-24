import React from 'react';

import type { ChartUIBlock } from '../../../lib/uiBlocks';
import { BlockFrame } from './BlockFrame';

/**
 * Small charts as inline SVG (JAN-252): the bundle has no chart library and this adds none.
 * Series colors are fixed categorical slots in order (validated dark-surface palette), never
 * cycled: a pie folds slices past the last slot into "Other". Every mark carries a native
 * tooltip, a legend names two or more series, and a screen-reader table holds the numbers.
 */
const SERIES_COLORS = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181'];
const OTHER_COLOR = '#8a8a85';
const WIDTH = 320;
const HEIGHT = 160;
const PAD = { top: 8, right: 8, bottom: 22, left: 34 };
const PLOT_W = WIDTH - PAD.left - PAD.right;
const PLOT_H = HEIGHT - PAD.top - PAD.bottom;

const format = (value: number, unit?: string) =>
  `${Number.isInteger(value) ? value : value.toFixed(2)}${unit ? ` ${unit}` : ''}`;

const useScale = (block: ChartUIBlock) => {
  const values = block.series.flatMap((series) => series.values);
  const max = Math.max(0, ...values);
  const min = Math.min(0, ...values);
  const span = max - min || 1;
  const y = (value: number) => PAD.top + PLOT_H - ((value - min) / span) * PLOT_H;
  return { max, min, y };
};

/** Shows at most ~6 x labels so they never collide. */
const labelStep = (count: number) => Math.max(1, Math.ceil(count / 6));

const Axes: React.FC<{ block: ChartUIBlock; x: (index: number) => number }> = ({ block, x }) => {
  const { max, min, y } = useScale(block);
  const step = labelStep(block.labels.length);
  return (
    <g className="fill-gray-500 text-[9px] dark:fill-gray-400" aria-hidden="true">
      <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(0)} y2={y(0)} className="stroke-gray-400/60" />
      <text x={PAD.left - 4} y={y(max) + 3} textAnchor="end">
        {format(max)}
      </text>
      {min < 0 && (
        <text x={PAD.left - 4} y={y(min) + 3} textAnchor="end">
          {format(min)}
        </text>
      )}
      {block.labels.map((label, index) =>
        index % step === 0 ? (
          <text key={index} x={x(index)} y={HEIGHT - 6} textAnchor="middle">
            {label.length > 10 ? `${label.slice(0, 9)}…` : label}
          </text>
        ) : null
      )}
    </g>
  );
};

const BarMarks: React.FC<{ block: ChartUIBlock }> = ({ block }) => {
  const { y } = useScale(block);
  const groupW = PLOT_W / block.labels.length;
  const barW = Math.max(1, (groupW - 2) / block.series.length - 2);
  const x = (index: number) => PAD.left + groupW * index + groupW / 2;
  return (
    <>
      <Axes block={block} x={x} />
      {block.series.map((series, seriesIndex) =>
        series.values.map((value, index) => {
          const left = PAD.left + groupW * index + 1 + seriesIndex * (barW + 2);
          const top = Math.min(y(value), y(0));
          return (
            <rect
              key={`${seriesIndex}-${index}`}
              x={left}
              y={top}
              width={barW}
              height={Math.max(1, Math.abs(y(value) - y(0)))}
              rx={Math.min(4, barW / 2)}
              fill={SERIES_COLORS[seriesIndex]}
            >
              <title>{`${series.name}, ${block.labels[index]}: ${format(value, block.unit)}`}</title>
            </rect>
          );
        })
      )}
    </>
  );
};

const LineMarks: React.FC<{ block: ChartUIBlock }> = ({ block }) => {
  const { y } = useScale(block);
  const count = block.labels.length;
  const x = (index: number) => PAD.left + (count === 1 ? PLOT_W / 2 : (PLOT_W * index) / (count - 1));
  return (
    <>
      <Axes block={block} x={x} />
      {block.series.map((series, seriesIndex) => (
        <g key={seriesIndex}>
          <polyline
            fill="none"
            stroke={SERIES_COLORS[seriesIndex]}
            strokeWidth={2}
            strokeLinejoin="round"
            points={series.values.map((value, index) => `${x(index)},${y(value)}`).join(' ')}
          />
          {series.values.map((value, index) => (
            <circle key={index} cx={x(index)} cy={y(value)} r={3} fill={SERIES_COLORS[seriesIndex]}>
              <title>{`${series.name}, ${block.labels[index]}: ${format(value, block.unit)}`}</title>
            </circle>
          ))}
        </g>
      ))}
    </>
  );
};

/** Pie slices in label order; labels past the palette fold into one "Other" slice. */
const pieSlices = (block: ChartUIBlock) => {
  const values = block.series[0]?.values ?? [];
  const slices = values.slice(0, SERIES_COLORS.length).map((value, index) => ({
    label: block.labels[index],
    value,
    color: SERIES_COLORS[index],
  }));
  const rest = values.slice(SERIES_COLORS.length).reduce((sum, value) => sum + value, 0);
  if (values.length > SERIES_COLORS.length) slices.push({ label: 'Other', value: rest, color: OTHER_COLOR });
  return slices.filter((slice) => slice.value > 0);
};

const arc = (start: number, end: number, r: number, cx: number, cy: number) => {
  const point = (angle: number) => `${cx + r * Math.sin(angle)},${cy - r * Math.cos(angle)}`;
  if (end - start >= Math.PI * 2 - 1e-9) return `M${cx},${cy - r}A${r},${r} 0 1 1 ${cx - 0.01},${cy - r}Z`;
  return `M${cx},${cy}L${point(start)}A${r},${r} 0 ${end - start > Math.PI ? 1 : 0} 1 ${point(end)}Z`;
};

const PieMarks: React.FC<{ block: ChartUIBlock }> = ({ block }) => {
  const slices = pieSlices(block);
  const total = slices.reduce((sum, slice) => sum + slice.value, 0) || 1;
  const ends = slices.map((_, index) =>
    slices.slice(0, index + 1).reduce((sum, slice) => sum + (slice.value / total) * Math.PI * 2, 0)
  );
  return (
    <g>
      {slices.map((slice, index) => {
        const start = index === 0 ? 0 : ends[index - 1];
        const angle = ends[index];
        return (
          <path
            key={index}
            d={arc(start, angle, 64, WIDTH / 2, HEIGHT / 2)}
            fill={slice.color}
            className="stroke-white dark:stroke-gray-900"
            strokeWidth={2}
          >
            <title>{`${slice.label}: ${format(slice.value, block.unit)} (${Math.round((slice.value / total) * 100)}%)`}</title>
          </path>
        );
      })}
    </g>
  );
};

const Legend: React.FC<{ items: Array<{ label: string; color: string }> }> = ({ items }) => (
  <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px]" aria-hidden="true">
    {items.map((item, index) => (
      <li key={index} className="flex items-center gap-1">
        <span className="inline-block h-2 w-2 rounded-sm" style={{ backgroundColor: item.color }} />
        {item.label}
      </li>
    ))}
  </ul>
);

const DataTable: React.FC<{ block: ChartUIBlock }> = ({ block }) => (
  <table className="sr-only">
    <thead>
      <tr>
        <th scope="col">Label</th>
        {block.series.map((series, index) => (
          <th key={index} scope="col">
            {series.name}
          </th>
        ))}
      </tr>
    </thead>
    <tbody>
      {block.labels.map((label, index) => (
        <tr key={index}>
          <th scope="row">{label}</th>
          {block.series.map((series, seriesIndex) => (
            <td key={seriesIndex}>{format(series.values[index], block.unit)}</td>
          ))}
        </tr>
      ))}
    </tbody>
  </table>
);

export const ChartBlock: React.FC<{ block: ChartUIBlock }> = ({ block }) => {
  const legend =
    block.kind === 'pie'
      ? pieSlices(block).map((slice) => ({ label: slice.label, color: slice.color }))
      : block.series.length > 1
        ? block.series.map((series, index) => ({ label: series.name, color: SERIES_COLORS[index] }))
        : [];
  const Marks = block.kind === 'pie' ? PieMarks : block.kind === 'line' ? LineMarks : BarMarks;
  return (
    <BlockFrame kind="Chart" title={block.title ?? (block.series.length === 1 ? block.series[0].name : undefined)}>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="h-auto w-full max-w-md" role="img" aria-label={`${block.kind} chart`}>
        <Marks block={block} />
      </svg>
      {legend.length > 0 && <Legend items={legend} />}
      <DataTable block={block} />
    </BlockFrame>
  );
};
