import React from 'react';
import { AlertTriangle } from 'lucide-react';

import { parseUIBlock, type UIBlock } from '../../../lib/uiBlocks';
import { ActionsBlock } from './ActionsBlock';
import { CardBlock } from './CardBlock';
import { ChartBlock } from './ChartBlock';
import { ChecklistBlock } from './ChecklistBlock';
import { ImageBlock } from './ImageBlock';
import { KeyValueBlock } from './KeyValueBlock';
import { TableBlock } from './TableBlock';

interface UIBlockViewProps {
  /** A raw block from a `__UI_BLOCK__` frame or a stored row's `payload`; validated here. */
  block: unknown;
  /** Sends an action button's chat message (`ChatSession.sendText`). */
  onAction?: (text: string) => boolean;
}

const prettyJson = (value: unknown): string => {
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
};

/** A block the client cannot render: its JSON as plain text, never interpreted. */
const UnrenderableBlock: React.FC<{ block: unknown }> = ({ block }) => (
  <section
    aria-label="Unrenderable block"
    className="not-prose mt-2 rounded-lg border border-amber-300/80 bg-amber-50/60 p-2 text-xs dark:border-amber-800 dark:bg-amber-950/30"
  >
    <p className="mb-1 flex items-center gap-1 font-medium text-amber-800 dark:text-amber-300">
      <AlertTriangle size={12} aria-hidden="true" />
      Could not render this block
    </p>
    <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px]">{prettyJson(block)}</pre>
  </section>
);

const renderBlock = (block: UIBlock, onAction: UIBlockViewProps['onAction']) => {
  switch (block.type) {
    case 'card':
      return <CardBlock block={block} />;
    case 'table':
      return <TableBlock block={block} />;
    case 'chart':
      return <ChartBlock block={block} />;
    case 'checklist':
      return <ChecklistBlock block={block} />;
    case 'key_value':
      return <KeyValueBlock block={block} />;
    case 'actions':
      return <ActionsBlock block={block} onAction={onAction} />;
    case 'image':
      return <ImageBlock block={block} />;
  }
};

/** One chat UI block (JAN-252): validated with the zod catalog, or shown as JSON when it does not parse. */
export const UIBlockView: React.FC<UIBlockViewProps> = ({ block, onAction }) => {
  const parsed = parseUIBlock(block);
  return parsed ? renderBlock(parsed, onAction) : <UnrenderableBlock block={block} />;
};

/** A reply's blocks, in render order. */
export const UIBlockList: React.FC<{ blocks: unknown[] | undefined; onAction?: (text: string) => boolean }> = ({
  blocks,
  onAction,
}) =>
  blocks && blocks.length > 0 ? (
    <div className="space-y-2">
      {blocks.map((block, index) => (
        <UIBlockView key={index} block={block} onAction={onAction} />
      ))}
    </div>
  ) : null;
