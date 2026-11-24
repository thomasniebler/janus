import React from 'react';

import type { CardUIBlock } from '../../../lib/uiBlocks';
import { BlockFrame } from './BlockFrame';
import { BlockLink } from './BlockLink';

/** A summary card: title, subtitle, body text, label/value fields, and links. */
export const CardBlock: React.FC<{ block: CardUIBlock }> = ({ block }) => (
  <BlockFrame kind="Card">
    <h4 className="text-sm font-semibold">{block.title}</h4>
    {block.subtitle && <p className="text-xs text-gray-500 dark:text-gray-400">{block.subtitle}</p>}
    {block.body && <p className="mt-2 whitespace-pre-wrap">{block.body}</p>}
    {block.fields && block.fields.length > 0 && (
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        {block.fields.map((field, index) => (
          <React.Fragment key={index}>
            <dt className="font-medium text-gray-500 dark:text-gray-400">{field.label}</dt>
            <dd className="break-words">{field.value}</dd>
          </React.Fragment>
        ))}
      </dl>
    )}
    {block.links && block.links.length > 0 && (
      <ul className="mt-2 flex flex-wrap gap-3 text-xs">
        {block.links.map((link, index) => (
          <li key={index}>
            <BlockLink label={link.label} href={link.href} />
          </li>
        ))}
      </ul>
    )}
  </BlockFrame>
);
