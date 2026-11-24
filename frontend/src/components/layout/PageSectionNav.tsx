import React from 'react';
import type { LucideIcon } from 'lucide-react';
import { NavBadge } from './NavBadge';

export interface PageSectionNavItem {
  id: string;
  name: string;
  icon: LucideIcon;
  tabId?: string;
  panelId?: string;
  /** Count shown next to the entry, e.g. suggested automations on Triggers (JAN-235). */
  badge?: number;
  badgeLabel?: string;
}

export interface PageSectionNavSection {
  title?: string;
  items: PageSectionNavItem[];
}

interface PageSectionNavProps {
  sections: PageSectionNavSection[];
  activeId: string;
  onSelect: (id: string) => void;
  ariaLabel: string;
}

export const PageSectionNav: React.FC<PageSectionNavProps> = ({ sections, activeId, onSelect, ariaLabel }) => {
  const items = React.useMemo(() => sections.flatMap((section) => section.items), [sections]);
  const itemRefs = React.useRef<Record<string, HTMLButtonElement | null>>({});

  const moveFocus = (nextIndex: number) => {
    const nextItem = items[nextIndex];
    if (!nextItem) {
      return;
    }
    onSelect(nextItem.id);
    itemRefs.current[nextItem.id]?.focus();
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, itemId: string) => {
    const currentIndex = items.findIndex((item) => item.id === itemId);
    if (currentIndex === -1) {
      return;
    }

    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      event.preventDefault();
      moveFocus((currentIndex + 1) % items.length);
      return;
    }

    if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      event.preventDefault();
      moveFocus((currentIndex - 1 + items.length) % items.length);
      return;
    }

    if (event.key === 'Home') {
      event.preventDefault();
      moveFocus(0);
      return;
    }

    if (event.key === 'End') {
      event.preventDefault();
      moveFocus(items.length - 1);
    }
  };

  return (
    <nav
      className="rounded-3xl border border-gray-200/80 bg-white/80 p-2.5 shadow-sm dark:border-gray-800 dark:bg-dark-card/80"
      aria-label={ariaLabel}
      role="tablist"
    >
      {sections.map((section, sectionIndex) => (
        <div
          key={section.title ?? `section-${sectionIndex}`}
          className={sectionIndex > 0 ? 'mt-2.5 border-t border-gray-200 pt-2.5 dark:border-gray-800' : undefined}
        >
          {section.title && (
            <div className="mb-2 px-1">
              <span className="text-[10px] font-bold uppercase tracking-widest text-gray-400 dark:text-gray-500">
                {section.title}
              </span>
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            {section.items.map((item) => (
              <button
                key={item.id}
                type="button"
                ref={(node) => {
                  itemRefs.current[item.id] = node;
                }}
                onClick={() => onSelect(item.id)}
                onKeyDown={(event) => handleKeyDown(event, item.id)}
                id={item.tabId}
                role="tab"
                aria-selected={activeId === item.id}
                aria-controls={item.panelId}
                tabIndex={activeId === item.id ? 0 : -1}
                className={`inline-flex items-center gap-2 rounded-2xl px-3 py-2 text-sm font-bold transition-all uiv2:font-terminal uiv2:text-[11px] uiv2:uppercase uiv2:tracking-[0.14em] uiv3:font-typewriter uiv3:text-[11px] uiv3:uppercase uiv3:tracking-[0.12em] ${
                  activeId === item.id
                    ? 'bg-primary text-white shadow-lg shadow-primary/20 uiv2:bg-v2-accent uiv2:text-v2-bg uiv2:shadow-[0_0_14px_0_rgba(198,242,74,0.4)] uiv3:border uiv3:border-v3-accent uiv3:bg-v3-accent uiv3:text-[#1a0b07] uiv3:shadow-none'
                    : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 uiv2:border uiv2:border-v2-border uiv2:text-v2-text-muted uiv2:hover:border-v2-accent uiv2:hover:bg-v2-accent-soft uiv2:hover:text-v2-accent uiv3:border uiv3:border-v3-border uiv3:text-v3-text-muted uiv3:hover:border-v3-accent uiv3:hover:bg-v3-accent-soft uiv3:hover:text-v3-accent-bright'
                }`}
              >
                <item.icon size={18} />
                {item.name}
                <NavBadge count={item.badge} label={item.badgeLabel ?? ''} />
              </button>
            ))}
          </div>
        </div>
      ))}
    </nav>
  );
};
