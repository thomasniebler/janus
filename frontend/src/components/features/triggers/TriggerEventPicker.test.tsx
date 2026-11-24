import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { TriggerSubsystemSpec } from '../../../types';
import { TriggerEventPicker } from './TriggerEventPicker';
import { describeCatalogEvent, findCatalogEvent, firstCatalogEvent } from './triggerEventCatalog';

const catalog: TriggerSubsystemSpec[] = [
  {
    subsystem: 'mail',
    label: 'Mail',
    description: 'Email',
    events: [
      { event_type: 'mail.received', label: 'New email received', description: 'New mail', payload: [{ name: 'subject', description: 'Subject' }] },
    ],
  },
  {
    subsystem: 'planner',
    label: 'Planner',
    description: 'Calendar',
    events: [
      { event_type: 'planner.synced', label: 'Calendar synced', description: 'Sync done', payload: [] },
      { event_type: 'planner.failed', label: 'Calendar sync failed', description: 'Sync failed', payload: [] },
    ],
  },
];

const options = (element: HTMLElement) => within(element).getAllByRole('option').map((option) => option.textContent);

describe('TriggerEventPicker', () => {
  it('only offers the events of the chosen subsystem', () => {
    render(<TriggerEventPicker catalog={catalog} value="planner.failed" onChange={() => undefined} />);

    expect(screen.getByLabelText('Subsystem')).toHaveValue('planner');
    expect(options(screen.getByLabelText('Event'))).toEqual(['Choose an event', 'Calendar synced', 'Calendar sync failed']);
    expect(screen.getByLabelText('Event')).toHaveValue('planner.failed');
  });

  it('switching the subsystem selects that subsystem\'s first event', async () => {
    const onChange = vi.fn();
    render(<TriggerEventPicker catalog={catalog} value="mail.received" onChange={onChange} />);

    await userEvent.setup().selectOptions(screen.getByLabelText('Subsystem'), 'planner');

    expect(onChange).toHaveBeenCalledWith('planner.synced');
  });

  it('flags a stored value the backend does not publish and selects nothing', () => {
    render(<TriggerEventPicker catalog={catalog} value="calendar.changed" onChange={() => undefined} />);

    expect(screen.getByLabelText('Subsystem')).toHaveValue('');
    expect(screen.getByLabelText('Event')).toBeDisabled();
    expect(screen.getByTestId('trigger-event-details')).toHaveTextContent(/calendar\.changed.*not an event Janus publishes/);
  });

  it('describes catalogued events by label and falls back to the raw value', () => {
    expect(findCatalogEvent(catalog, 'planner.synced')?.subsystem.subsystem).toBe('planner');
    expect(firstCatalogEvent(catalog)).toBe('mail.received');
    expect(firstCatalogEvent([])).toBeNull();
    expect(describeCatalogEvent(catalog, 'mail.received')).toBe('Mail · New email received');
    expect(describeCatalogEvent(catalog, 'calendar.changed')).toBe('calendar.changed');
  });
});
