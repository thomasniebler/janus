import React, { useState } from 'react';
import { clsx, type ClassValue } from 'clsx';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { twMerge } from 'tailwind-merge';
import { Card } from './Card';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

interface CollapsibleCardProps extends React.HTMLAttributes<HTMLDivElement> {
  title: string;
  icon: React.ReactNode;
  badge?: React.ReactNode;
  children: React.ReactNode;
  contentClassName?: string;
  defaultCollapsed?: boolean;
  collapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
}

export const CollapsibleCard: React.FC<CollapsibleCardProps> = ({
  title,
  icon,
  badge,
  children,
  className,
  contentClassName,
  defaultCollapsed = false,
  collapsed: controlledCollapsed,
  onCollapsedChange,
  ...props
}) => {
  const [uncontrolledCollapsed, setUncontrolledCollapsed] = useState(defaultCollapsed);
  const collapsed = controlledCollapsed ?? uncontrolledCollapsed;

  const handleToggle = () => {
    const nextCollapsed = !collapsed;
    if (controlledCollapsed === undefined) {
      setUncontrolledCollapsed(nextCollapsed);
    }
    onCollapsedChange?.(nextCollapsed);
  };

  return (
    <Card className={className} data-collapsed={collapsed ? 'true' : 'false'} {...props}>
      <div
        className={cn('flex items-center justify-between gap-3 px-5', collapsed ? 'py-3' : 'py-4 border-b')}
        style={collapsed ? undefined : { borderBottomColor: 'var(--janus-card-border)' }}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-primary">{icon}</span>
          <h2 className="text-lg font-semibold">{title}</h2>
          {badge}
        </div>
        <button
          type="button"
          onClick={handleToggle}
          className="inline-flex items-center gap-2 rounded-[6px] px-3 py-1.5 text-sm font-medium transition-colors text-[rgba(140,170,220,0.6)] hover:bg-[rgba(59,130,246,0.10)] hover:text-[#93c5fd] uiv2:border uiv2:border-v2-border uiv2:font-terminal uiv2:text-[10px] uiv2:uppercase uiv2:tracking-[0.18em] uiv2:text-v2-text-muted uiv2:hover:border-v2-accent uiv2:hover:bg-v2-accent-soft uiv2:hover:text-v2-accent uiv3:border uiv3:border-v3-border uiv3:font-typewriter uiv3:text-[10px] uiv3:uppercase uiv3:tracking-[0.16em] uiv3:text-v3-text-muted uiv3:hover:border-v3-accent uiv3:hover:bg-v3-accent-soft uiv3:hover:text-v3-accent-bright"
          aria-label={`${collapsed ? 'Expand' : 'Collapse'} ${title}`}
          aria-expanded={!collapsed}
        >
          {collapsed ? 'Expand' : 'Collapse'}
          {collapsed ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
        </button>
      </div>

      {!collapsed && (
        <div className={contentClassName}>{children}</div>
      )}
    </Card>
  );
};
