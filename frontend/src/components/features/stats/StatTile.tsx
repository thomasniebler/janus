import React from 'react';

/** One headline number on the Stats page, with an optional detail line. */
export const StatTile: React.FC<{ label: string; value: string | number; detail?: string }> = ({ label, value, detail }) => (
  <div className="rounded-xl border border-gray-200 p-4 dark:border-gray-800 uiv2:rounded-none uiv3:rounded-none" data-testid={`stat-${label}`}>
    <p className="text-xs font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400">{label}</p>
    <p className="mt-1 text-2xl font-bold tabular-nums">{value}</p>
    {detail && <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">{detail}</p>}
  </div>
);
