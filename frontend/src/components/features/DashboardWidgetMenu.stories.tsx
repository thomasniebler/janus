import type { Meta, StoryObj } from '@storybook/react';
import { expect, fn, userEvent, within } from 'storybook/test';
import type { DashboardWidgetPreference } from '../../types';
import { DashboardWidgetMenu } from './DashboardWidgetMenu';

const widget = (widget_key: DashboardWidgetPreference['widget_key'], is_visible = true): DashboardWidgetPreference => ({
  widget_key,
  is_visible,
  is_collapsed: false,
  prompt_override: null,
  briefing_email_window: null,
});

const allShown = (['briefing', 'topic_focus', 'important_emails', 'whatsapp_messages', 'schedule', 'tasks'] as const).map(
  (key) => widget(key)
);

const meta = {
  title: 'Features/DashboardWidgetMenu',
  component: DashboardWidgetMenu,
  args: { preferences: allShown, onVisibilityChange: fn(), defaultOpen: true },
  decorators: [(Story) => <div style={{ minHeight: 480 }}><Story /></div>],
} satisfies Meta<typeof DashboardWidgetMenu>;

export default meta;

type Story = StoryObj<typeof meta>;

/** The closed menu: just the button. */
export const Closed: Story = { args: { defaultOpen: false } };

/** Every widget shown. */
export const AllShown: Story = {};

/** Some widgets hidden; clicking a toggle reports the new visibility. */
export const SomeHidden: Story = {
  args: { preferences: allShown.map((pref) => (['whatsapp_messages', 'tasks'].includes(pref.widget_key) ? { ...pref, is_visible: false } : pref)) },
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole('checkbox', { name: /^Active Tasks/ }));
    await expect(args.onVisibilityChange).toHaveBeenCalledWith('tasks', true);
  },
};

/** The saved preferences are still loading: the toggles are disabled and say so (JAN-225). */
export const Loading: Story = {
  args: { loading: true },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText('Loading your widget settings…')).toBeInTheDocument();
    await expect(canvas.getByRole('checkbox', { name: /^Briefing/ })).toBeDisabled();
  },
};
