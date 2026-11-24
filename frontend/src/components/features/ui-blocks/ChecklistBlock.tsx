import React from 'react';
import { CheckSquare, Square } from 'lucide-react';

import type { ChecklistUIBlock } from '../../../lib/uiBlocks';
import { BlockFrame } from './BlockFrame';

/** A read-only checklist: the state is shown with an icon and in words, never toggled. */
export const ChecklistBlock: React.FC<{ block: ChecklistUIBlock }> = ({ block }) => (
  <BlockFrame kind="Checklist" title={block.title}>
    <ul className="space-y-1">
      {block.items.map((item, index) => (
        <li key={index} className="flex items-start gap-2">
          {item.checked ? (
            <CheckSquare size={14} className="mt-0.5 shrink-0 text-emerald-500" aria-hidden="true" />
          ) : (
            <Square size={14} className="mt-0.5 shrink-0 text-gray-400" aria-hidden="true" />
          )}
          <span className={item.checked ? 'text-gray-500 line-through dark:text-gray-400' : ''}>{item.text}</span>
          <span className="sr-only">{item.checked ? '(done)' : '(open)'}</span>
        </li>
      ))}
    </ul>
  </BlockFrame>
);
