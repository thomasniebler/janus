import React from 'react';

interface NavBadgeProps {
  /** Nothing is rendered for 0 or less. */
  count?: number;
  /** Spoken label, e.g. "2 suggested automations awaiting review". */
  label: string;
}

/**
 * Small count pill next to a navigation entry, shared by all three shells and
 * the Settings section nav (JAN-235: suggested automations awaiting review).
 */
export const NavBadge: React.FC<NavBadgeProps> = ({ count = 0, label }) => {
  if (count <= 0) return null;
  return (
    <span
      data-testid="nav-badge"
      aria-label={label}
      title={label}
      className="ml-auto inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-amber-500 px-1.5 text-[10px] font-bold leading-5 text-white uiv2:rounded-none uiv2:bg-v2-accent uiv2:text-v2-bg uiv3:rounded-none uiv3:bg-v3-accent uiv3:text-[#1a0b07]"
    >
      {count > 99 ? '99+' : count}
    </span>
  );
};
