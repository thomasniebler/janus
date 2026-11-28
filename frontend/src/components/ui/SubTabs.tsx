import React from 'react';

export interface SubTab {
  id: string;
  name: string;
}

interface SubTabsProps {
  tabs: readonly SubTab[];
  activeId: string;
  onSelect: (id: string) => void;
  ariaLabel: string;
  /** Prefix for the generated `id`s: the tab gets `<idPrefix>-tab-<id>`, its panel `<idPrefix>-panel-<id>`. */
  idPrefix: string;
}

/** Second-level tabs inside a page (Settings → Connections): a compact underline row with arrow-key navigation. */
export const SubTabs: React.FC<SubTabsProps> = ({ tabs, activeId, onSelect, ariaLabel, idPrefix }) => {
  const refs = React.useRef<Record<string, HTMLButtonElement | null>>({});

  const moveFocus = (index: number) => {
    const next = tabs[(index + tabs.length) % tabs.length];
    onSelect(next.id);
    refs.current[next.id]?.focus();
  };

  const handleKeyDown = (event: React.KeyboardEvent, index: number) => {
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      event.preventDefault();
      moveFocus(index + 1);
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      event.preventDefault();
      moveFocus(index - 1);
    } else if (event.key === 'Home') {
      event.preventDefault();
      moveFocus(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      moveFocus(tabs.length - 1);
    }
  };

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className="flex flex-wrap gap-1 border-b border-gray-200 dark:border-gray-800"
    >
      {tabs.map((tab, index) => {
        const active = tab.id === activeId;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            ref={(node) => {
              refs.current[tab.id] = node;
            }}
            id={`${idPrefix}-tab-${tab.id}`}
            aria-selected={active}
            aria-controls={`${idPrefix}-panel-${tab.id}`}
            tabIndex={active ? 0 : -1}
            onClick={() => onSelect(tab.id)}
            onKeyDown={(event) => handleKeyDown(event, index)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-semibold transition-colors ${
              active
                ? 'border-primary text-primary'
                : 'border-transparent text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'
            }`}
          >
            {tab.name}
          </button>
        );
      })}
    </div>
  );
};
