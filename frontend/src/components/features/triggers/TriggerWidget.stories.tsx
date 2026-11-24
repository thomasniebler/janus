import type { Meta, StoryObj } from '@storybook/react';
import type { FC, ReactNode } from 'react';

import type { RuntimeTrigger } from '../../../types';
import { TriggerWidget, type TriggerWidgetStyle } from './TriggerWidget';
import { TriggerWidgetList } from './TriggerWidgetList';
import { makeTrigger, triggerMatrix, triggerSample } from './triggerFixtures';
import { RUNTIME_TRIGGER_KINDS, RUNTIME_TRIGGER_STATUSES } from './triggerPresentation';

/**
 * JAN-187 style exploration.
 *
 * Three genuinely different treatments of the same runtime-trigger data, each
 * shown across every status x kind combination and across all three app shells,
 * so the design can be picked by looking rather than by guessing.
 */

type Shell = 'v1' | 'v2' | 'v3';

const SHELL_LABEL: Record<Shell, string> = {
  v1: 'UIv1 — Classic glass',
  v2: 'UIv2 — Terminal HUD',
  v3: 'UIv3 — Rusty Console',
};

const SHELL_CLASS: Record<Shell, string> = {
  v1: 'dark bg-[#04091c] text-[#e8f0ff]',
  v2: 'ui-v2 dark bg-v2-bg-deep text-v2-text',
  v3: 'ui-v3 dark bg-v3-bg-deep text-v3-text font-typewriter',
};

const STYLE_LABEL: Record<TriggerWidgetStyle, string> = {
  row: 'Style A — Compact ledger row',
  card: 'Style B — Dashboard card',
  timeline: 'Style C — Lifecycle trace',
};

const STYLE_NOTE: Record<TriggerWidgetStyle, string> = {
  row: 'Densest option: one hairline-ruled line per trigger. Best when the list is long and the question is "is anything red?".',
  card: 'Status rail, badges and a four-cell lifecycle grid. Best when each trigger deserves its own object on the page.',
  timeline: 'Monospace log reader: every lifecycle step, including the ones not reached yet. Best for debugging one trigger.',
};

interface GalleryProps {
  shell: Shell;
  widgetStyle: TriggerWidgetStyle;
  triggers: RuntimeTrigger[];
  /** Render all three styles stacked instead of only `widgetStyle`. */
  compareStyles?: boolean;
  /** Render the chosen style once per shell instead of only in `shell`. */
  compareShells?: boolean;
}

const Panel: FC<{ shell: Shell; title: string; note?: string; children: ReactNode }> = ({
  shell,
  title,
  note,
  children,
}) => (
  <div className={`${SHELL_CLASS[shell]} space-y-3 p-6`}>
    <div>
      <h3 className="text-sm font-bold uppercase tracking-wider opacity-80">{title}</h3>
      {note && <p className="mt-1 max-w-2xl text-xs opacity-60">{note}</p>}
    </div>
    {children}
  </div>
);

const Gallery: FC<GalleryProps> = ({ shell, widgetStyle, triggers, compareStyles, compareShells }) => {
  const styles: TriggerWidgetStyle[] = compareStyles ? ['row', 'card', 'timeline'] : [widgetStyle];
  const shells: Shell[] = compareShells ? ['v1', 'v2', 'v3'] : [shell];
  return (
    <div className="min-h-screen">
      {shells.map((current) =>
        styles.map((style) => (
          <Panel
            key={`${current}-${style}`}
            shell={current}
            title={`${STYLE_LABEL[style]} · ${SHELL_LABEL[current]}`}
            note={STYLE_NOTE[style]}
          >
            <TriggerWidgetList triggers={triggers} widgetStyle={style} />
          </Panel>
        )),
      )}
    </div>
  );
};

const meta = {
  title: 'Features/Runtime triggers/TriggerWidget',
  component: Gallery,
  args: {
    shell: 'v1',
    widgetStyle: 'card',
    triggers: triggerMatrix,
    compareStyles: false,
    compareShells: false,
  },
  argTypes: {
    shell: { control: 'inline-radio', options: ['v1', 'v2', 'v3'] },
    widgetStyle: { control: 'inline-radio', options: ['row', 'card', 'timeline'] },
  },
  parameters: { layout: 'fullscreen' },
} satisfies Meta<typeof Gallery>;

export default meta;

type Story = StoryObj<typeof meta>;

/* ------------------------------------------------------------------ *
 * 1. The three candidate styles, each over the full status x kind grid
 * ------------------------------------------------------------------ */

export const StyleA_CompactRow: Story = {
  name: 'Style A — Compact ledger row',
  args: { widgetStyle: 'row' },
};

export const StyleB_DashboardCard: Story = {
  name: 'Style B — Dashboard card',
  args: { widgetStyle: 'card' },
};

export const StyleC_LifecycleTrace: Story = {
  name: 'Style C — Lifecycle trace',
  args: { widgetStyle: 'timeline' },
};

/* ------------------------------------------- *
 * 2. Side-by-side comparison for the design call
 * ------------------------------------------- */

export const AllStylesComparison: Story = {
  name: 'Compare all three styles',
  args: { compareStyles: true, triggers: triggerSample },
};

/* ------------------------------------ *
 * 3. Shell coverage (UIv1 / UIv2 / UIv3)
 * ------------------------------------ */

export const AcrossAllShells: Story = {
  name: 'Recommended style across all shells',
  args: { compareShells: true, widgetStyle: 'card', triggers: triggerSample },
};

export const EveryStyleInEveryShell: Story = {
  name: 'Every style in every shell',
  args: { compareShells: true, compareStyles: true, triggers: triggerSample },
};

/* --------------------------------- *
 * 4. Individual states worth a look
 * --------------------------------- */

export const FailureStates: Story = {
  name: 'Failure states (failed + exhausted)',
  args: {
    compareStyles: true,
    triggers: triggerMatrix.filter((trigger) => trigger.status === 'failed' || trigger.status === 'exhausted'),
  },
};

export const SingleWidgets: Story = {
  name: 'One widget per status',
  render: (args) => (
    <div className={`${SHELL_CLASS[args.shell]} min-h-screen space-y-4 p-6`}>
      {RUNTIME_TRIGGER_STATUSES.map((status) => (
        <TriggerWidget
          key={status}
          trigger={makeTrigger(status, 'manual')}
          widgetStyle={args.widgetStyle}
        />
      ))}
    </div>
  ),
};

export const SingleWidgetsByKind: Story = {
  name: 'One widget per kind',
  render: (args) => (
    <div className={`${SHELL_CLASS[args.shell]} min-h-screen space-y-4 p-6`}>
      {RUNTIME_TRIGGER_KINDS.map((kind) => (
        <TriggerWidget
          key={kind}
          trigger={makeTrigger('completed', kind)}
          widgetStyle={args.widgetStyle}
        />
      ))}
    </div>
  ),
};
