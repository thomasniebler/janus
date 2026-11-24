import React, { useEffect, useRef, useState } from 'react';
import { MoreVertical } from 'lucide-react';
import type { DashboardWidgetKey, DashboardWidgetPreference } from '../../types';
import { Card } from '../ui/Card';

const WIDGET_LABELS: Record<DashboardWidgetKey, string> = {
  briefing: 'Briefing',
  topic_focus: 'Topic Focus',
  important_emails: 'Important Emails',
  whatsapp_messages: 'WhatsApp',
  schedule: 'Schedule',
  tasks: 'Active Tasks',
};

const WIDGET_DESCRIPTIONS: Record<DashboardWidgetKey, string> = {
  briefing: 'Your AI-generated daily summary.',
  topic_focus: 'Tracked topics Janus thinks deserve ongoing attention.',
  important_emails: 'Recent important mail that needs attention.',
  whatsapp_messages: 'Recent WhatsApp messages from your configured recent-history window.',
  schedule: 'Today’s calendar events.',
  tasks: 'Open planner tasks.',
};

interface DashboardWidgetMenuProps {
  /** Every widget's effective preference, in menu order. */
  preferences: DashboardWidgetPreference[];
  /**
   * True until the user's saved preferences arrive. The toggles stay disabled meanwhile:
   * they would show the defaults, and a click would act on that wrong state (JAN-225).
   */
  loading?: boolean;
  onVisibilityChange: (widgetKey: DashboardWidgetKey, visible: boolean) => void;
  /** Start with the menu open (Storybook). */
  defaultOpen?: boolean;
}

/** The dashboard's "Widget options" menu: one show/hide toggle per widget. Closes on an outside click or Escape. */
export const DashboardWidgetMenu: React.FC<DashboardWidgetMenuProps> = ({
  preferences,
  loading = false,
  onVisibilityChange,
  defaultOpen = false,
}) => {
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [isOpen, setIsOpen] = useState(defaultOpen);

  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }

    const handlePointerDown = (event: MouseEvent) => {
      if (menuRef.current && event.target instanceof Node && !menuRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isOpen]);

  return (
    <div ref={menuRef} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        className="mt-1 inline-flex h-11 w-11 items-center justify-center rounded-xl border border-gray-200 bg-white text-gray-500 shadow-sm transition-colors hover:bg-gray-50 hover:text-primary dark:border-gray-700 dark:bg-dark-card dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-primary"
        aria-label="Configure dashboard widgets"
        aria-expanded={isOpen}
      >
        <MoreVertical size={18} />
      </button>

      {isOpen && (
        <Card className="absolute left-0 top-14 z-20 w-80 p-2 shadow-xl shadow-gray-900/10">
          <div className="px-3 pb-2 pt-1">
            <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">Widget options</p>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              {loading ? 'Loading your widget settings…' : 'Show or hide the widgets on your dashboard.'}
            </p>
          </div>

          <div className="space-y-1">
            {preferences.map(({ widget_key, is_visible }) => (
              <div key={widget_key} className="rounded-xl px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-800/60">
                <div className="flex items-start gap-3">
                  <input
                    id={`widget-toggle-${widget_key}`}
                    type="checkbox"
                    checked={is_visible}
                    disabled={loading}
                    onChange={(event) => onVisibilityChange(widget_key, event.target.checked)}
                    className="mt-1 h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
                  />
                  <label htmlFor={`widget-toggle-${widget_key}`} className="min-w-0 flex-1 cursor-pointer">
                    <span className="block text-sm font-medium text-gray-900 dark:text-gray-100">
                      {WIDGET_LABELS[widget_key]}
                    </span>
                    <span className="block text-xs text-gray-500 dark:text-gray-400">{WIDGET_DESCRIPTIONS[widget_key]}</span>
                  </label>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
};
