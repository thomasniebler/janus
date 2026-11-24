import React from 'react';

import { withAlpha } from '../../lib/agentColors';

const DELAYS = ['', '[animation-delay:0.2s]', '[animation-delay:0.4s]'];

/** The "Janus is typing" dots of an assistant message that has no content yet. */
export const TypingDots: React.FC<{ color: string }> = ({ color }) => (
  <div className="flex gap-1 py-1">
    {DELAYS.map((delay) => (
      <div
        key={delay || 'first'}
        className={`w-1.5 h-1.5 rounded-full animate-bounce ${delay}`}
        style={{ backgroundColor: withAlpha(color, '80') }}
      ></div>
    ))}
  </div>
);
