import React from 'react';

import type { TableUIBlock } from '../../../lib/uiBlocks';
import { BlockFrame } from './BlockFrame';

type Cell = TableUIBlock['rows'][number][number];

const formatCell = (cell: Cell): string => {
  if (cell === null) return '';
  if (typeof cell === 'boolean') return cell ? 'Yes' : 'No';
  return String(cell);
};

/** A small table of plain-text cells; scrolls sideways instead of widening the chat. */
export const TableBlock: React.FC<{ block: TableUIBlock }> = ({ block }) => (
  <BlockFrame kind="Table" title={block.title}>
    <div className="overflow-x-auto">
      <table className="min-w-full border-collapse text-left text-xs">
        <thead>
          <tr>
            {block.columns.map((column, index) => (
              <th
                key={index}
                scope="col"
                className="border-b border-gray-200 px-2 py-1 font-semibold dark:border-gray-700 uiv2:border-v2-border uiv3:border-v3-border"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="odd:bg-gray-50/60 dark:odd:bg-gray-800/40">
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className="px-2 py-1 align-top break-words">
                  {formatCell(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    {block.rows.length === 0 && <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">No rows.</p>}
  </BlockFrame>
);
