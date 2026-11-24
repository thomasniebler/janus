import React, { useState } from 'react';

import { actionMessage, formatActionArgs, type ActionsUIBlock, type UIBlockAction } from '../../../lib/uiBlocks';
import { BlockFrame } from './BlockFrame';

const STYLE_CLASSES: Record<UIBlockAction['style'], string> = {
  primary:
    'border-primary bg-primary text-white hover:opacity-90 uiv2:border-v2-accent uiv2:bg-v2-accent uiv2:text-v2-bg uiv3:border-v3-accent uiv3:bg-v3-accent',
  secondary:
    'border-gray-300 bg-white text-gray-800 hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700',
  danger: 'border-red-400 bg-white text-red-700 hover:bg-red-50 dark:border-red-800 dark:bg-gray-800 dark:text-red-300',
};

interface ActionsBlockProps {
  block: ActionsUIBlock;
  /** Sends the button's chat message; returns false when it could not be sent. Absent: buttons are disabled. */
  onAction?: (text: string) => boolean;
}

/**
 * Action buttons. A click never runs anything here: it sends an ordinary chat message
 * naming the button, the tool, and its arguments, so the tool runs (if at all) in a normal
 * chat turn under the tool policy and the confirmation gate. Each button works once.
 */
export const ActionsBlock: React.FC<ActionsBlockProps> = ({ block, onAction }) => {
  const [sent, setSent] = useState<ReadonlySet<number>>(new Set());
  return (
    <BlockFrame kind="Actions" title={block.title}>
      <div className="flex flex-wrap gap-2">
        {block.buttons.map((button, index) => (
          <button
            key={index}
            type="button"
            disabled={!onAction || sent.has(index)}
            title={`Asks Janus to run ${button.tool}${formatActionArgs(button.args)}`}
            onClick={() => {
              if (onAction?.(actionMessage(button))) setSent((previous) => new Set(previous).add(index));
            }}
            className={`rounded-md border px-3 py-1 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50 uiv2:rounded-none ${STYLE_CLASSES[button.style]}`}
          >
            {button.label}
          </button>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-gray-500 dark:text-gray-400">
        A button sends Janus a chat message asking for that action; tools that need your OK still ask first.
      </p>
    </BlockFrame>
  );
};
