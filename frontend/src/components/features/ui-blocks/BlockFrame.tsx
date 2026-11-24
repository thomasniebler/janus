import React from 'react';

interface BlockFrameProps {
  /** What the block is, for its accessible name ("Table", "Chart", ...). */
  kind: string;
  title?: string;
  children: React.ReactNode;
}

/** The shared frame of every UI block (JAN-252): a labelled region with an optional title, all text as text. */
export const BlockFrame: React.FC<BlockFrameProps> = ({ kind, title, children }) => (
  <section
    aria-label={title ? `${kind}: ${title}` : kind}
    className="not-prose mt-2 overflow-hidden rounded-lg border border-gray-200 bg-white/70 p-3 text-sm text-gray-800 dark:border-gray-700 dark:bg-gray-900/60 dark:text-gray-200 uiv2:rounded-none uiv2:border-v2-border uiv2:bg-v2-surface uiv2:text-v2-text uiv3:border-v3-border uiv3:bg-v3-surface uiv3:text-v3-text"
  >
    {title && <h4 className="mb-2 text-sm font-semibold">{title}</h4>}
    {children}
  </section>
);
