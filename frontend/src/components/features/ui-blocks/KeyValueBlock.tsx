import React from 'react';

import type { KeyValueUIBlock } from '../../../lib/uiBlocks';
import { BlockFrame } from './BlockFrame';

const formatValue = (value: KeyValueUIBlock['items'][number]['value']): string =>
  typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value);

/** A list of key/value facts. */
export const KeyValueBlock: React.FC<{ block: KeyValueUIBlock }> = ({ block }) => (
  <BlockFrame kind="Details" title={block.title}>
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
      {block.items.map((item, index) => (
        <React.Fragment key={index}>
          <dt className="font-medium text-gray-500 dark:text-gray-400">{item.key}</dt>
          <dd className="break-words">{formatValue(item.value)}</dd>
        </React.Fragment>
      ))}
    </dl>
  </BlockFrame>
);
