import React, { useId, useMemo, useState } from 'react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export interface ComboboxOption {
  value: string;
  label: string;
}

interface ComboboxProps {
  label?: string;
  value: string;
  onChange: (value: string) => void;
  options: ComboboxOption[];
  placeholder?: string;
  className?: string;
  /** Rendered under the input, e.g. a loading or status note. */
  hint?: React.ReactNode;
  /** Shown in the open list when nothing matches the typed text. */
  emptyMessage?: string;
  /** Caps rendered rows so very large catalogs stay responsive; typing narrows further. */
  maxVisibleOptions?: number;
}

/**
 * A minimal searchable combobox: a text input with a filtered option list.
 *
 * Typing both filters the list and updates `value`, so free-text entry is always
 * possible (the list is a suggestion aid, never a gate). Arrow keys move the
 * highlight, Enter picks it, Escape closes the list.
 */
export const Combobox: React.FC<ComboboxProps> = ({
  label,
  value,
  onChange,
  options,
  placeholder,
  className,
  hint,
  emptyMessage = 'No matching options. The typed value will be used as-is.',
  maxVisibleOptions = 200,
}) => {
  const inputId = useId();
  const listId = useId();
  const [open, setOpen] = useState(false);
  // `null` = the user has not typed since opening, so show every option.
  const [query, setQuery] = useState<string | null>(null);
  const [highlight, setHighlight] = useState(0);

  const filtered = useMemo(() => {
    const needle = (query ?? '').trim().toLowerCase();
    const matches = needle
      ? options.filter(
          (option) => option.value.toLowerCase().includes(needle) || option.label.toLowerCase().includes(needle)
        )
      : options;
    return matches.slice(0, maxVisibleOptions);
  }, [maxVisibleOptions, options, query]);

  const openList = () => {
    setOpen(true);
    setQuery(null);
    setHighlight(Math.max(0, options.findIndex((option) => option.value === value)));
  };

  const select = (option: ComboboxOption) => {
    onChange(option.value);
    setOpen(false);
    setQuery(null);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!open) {
        openList();
        return;
      }
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setHighlight((current) => {
        if (filtered.length === 0) return 0;
        return (current + step + filtered.length) % filtered.length;
      });
    } else if (event.key === 'Enter' && open && filtered[highlight]) {
      event.preventDefault();
      select(filtered[highlight]);
    } else if (event.key === 'Escape' && open) {
      event.preventDefault();
      setOpen(false);
    }
  };

  const activeOption = open ? filtered[highlight] : undefined;

  return (
    <div className={cn('space-y-1.5 w-full', className)}>
      {label && (
        <label htmlFor={inputId} className="text-xs font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider ml-1">
          {label}
        </label>
      )}
      <div className="relative">
        <input
          id={inputId}
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={activeOption ? `${listId}-${highlight}` : undefined}
          autoComplete="off"
          value={value}
          placeholder={placeholder}
          onFocus={openList}
          onClick={() => !open && openList()}
          onBlur={() => setOpen(false)}
          onKeyDown={onKeyDown}
          onChange={(event) => {
            onChange(event.target.value);
            setQuery(event.target.value);
            setHighlight(0);
            setOpen(true);
          }}
          className="block w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-gray-700 rounded-xl focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none text-sm transition-all placeholder:text-gray-400 dark:placeholder:text-gray-600"
        />
        {open && (
          <ul
            id={listId}
            role="listbox"
            aria-label={label ? `${label} options` : 'Options'}
            className="absolute z-20 mt-1 max-h-64 w-full overflow-auto rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-dark-card shadow-lg py-1 text-sm"
          >
            {filtered.length === 0 ? (
              <li className="px-4 py-2 text-gray-500 dark:text-gray-400">{emptyMessage}</li>
            ) : (
              filtered.map((option, index) => (
                <li
                  key={option.value}
                  id={`${listId}-${index}`}
                  role="option"
                  aria-selected={option.value === value}
                  // mousedown (not click) so the input does not blur and close the list first
                  onMouseDown={(event) => {
                    event.preventDefault();
                    select(option);
                  }}
                  onMouseEnter={() => setHighlight(index)}
                  className={cn(
                    'cursor-pointer px-4 py-2 flex flex-col',
                    index === highlight && 'bg-primary/10',
                    option.value === value && 'font-semibold'
                  )}
                >
                  <span>{option.label}</span>
                  {option.label !== option.value && (
                    <span className="font-mono text-xs text-gray-500 dark:text-gray-400">{option.value}</span>
                  )}
                </li>
              ))
            )}
          </ul>
        )}
      </div>
      {hint && <div className="text-xs text-gray-500 dark:text-gray-400 ml-1">{hint}</div>}
    </div>
  );
};
