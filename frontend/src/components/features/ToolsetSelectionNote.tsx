import React from 'react';

import type { ToolsetSelection } from '../../contexts/chatSession';

export interface ToolsetSelectionNoteProps {
  toolset: ToolsetSelection | null | undefined;
}

/**
 * The tools Janus picked for the current message and why (JAN-300). Shown only when the
 * selection actually narrowed the authorized set; a fallback offers every authorized tool.
 */
export const ToolsetSelectionNote: React.FC<ToolsetSelectionNoteProps> = ({ toolset }) => {
  if (!toolset || !toolset.narrowed || !toolset.tool_ids) return null;
  return (
    <div
      role="note"
      aria-label="Tool selection"
      className="max-h-20 overflow-y-auto rounded-md border border-gray-200/80 bg-gray-100/70 px-2.5 py-1.5 text-[11px] text-gray-600 dark:border-gray-700 dark:bg-gray-800/70 dark:text-gray-300"
    >
      <p>
        <span className="font-medium">
          Tools picked for this message ({toolset.tool_ids.length} of {toolset.authorized_count}):
        </span>{' '}
        {toolset.tool_ids.join(', ')}
      </p>
      {toolset.reason && <p>Why: {toolset.reason}</p>}
      {toolset.widened.length > 0 && <p>Added on request: {toolset.widened.join(', ')}</p>}
      {toolset.trimmed.length > 0 && <p>Not available here, dropped: {toolset.trimmed.join(', ')}</p>}
    </div>
  );
};
