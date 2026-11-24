import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { actionMessage, parseUIBlock, toUIBlockPayloads, UI_BLOCK_MAX_BYTES } from '../../../lib/uiBlocks';
import { UIBlockList, UIBlockView } from './UIBlockView';
import {
  actionsBlock,
  barChartBlock,
  cardBlock,
  checklistBlock,
  imageBlock,
  invalidBlocks,
  keyValueBlock,
  lineChartBlock,
  pieChartBlock,
  tableBlock,
} from './uiBlockFixtures';

const renderBlock = (block: unknown, onAction?: (text: string) => boolean) =>
  render(
    <MemoryRouter>
      <UIBlockView block={block} onAction={onAction} />
    </MemoryRouter>
  );

const expectFallback = (block: unknown) => {
  const fallback = screen.getByRole('region', { name: 'Unrenderable block' });
  expect(within(fallback).getByText('Could not render this block')).toBeInTheDocument();
  expect(fallback.querySelector('pre')?.textContent).toBe(JSON.stringify(block, null, 2));
};

describe('UI block catalog (JAN-252)', () => {
  it('renders a card with plain-text body, fields, and safe links', () => {
    renderBlock(cardBlock);
    const card = screen.getByRole('region', { name: 'Card' });
    expect(within(card).getByRole('heading', { name: 'Invoice overdue' })).toBeInTheDocument();
    // Model text is never HTML.
    expect(within(card).getByText('Payment of <b>120 EUR</b> was due on Friday.')).toBeInTheDocument();
    expect(card.querySelector('b')).toBeNull();
    expect(within(card).getByText('Amount')).toBeInTheDocument();
    expect(within(card).getByRole('link', { name: 'Open in Emails' })).toHaveAttribute('href', '/emails');
    const external = within(card).getByRole('link', { name: /Pay online/ });
    expect(external).toHaveAttribute('href', 'https://pay.example.com/invoice/42');
    expect(external).toHaveAttribute('target', '_blank');
    expect(external).toHaveAttribute('rel', 'noopener noreferrer');
    expect(external).toHaveTextContent('(pay.example.com)');
  });

  it('renders a table with one header per column and formatted cells', () => {
    renderBlock(tableBlock);
    const table = screen.getByRole('table');
    expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['Task', 'Due', 'Done']);
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows.map((row) => within(row).getAllByRole('cell').map((cell) => cell.textContent))).toEqual([
      ['Pay invoice', 'Fri', 'No'],
      ['Call Anna', '', 'Yes'],
      ['Book flights', 'Mon', 'No'],
    ]);
  });

  it.each([
    ['bar', barChartBlock, 'rect', 10],
    ['line', lineChartBlock, 'circle', 5],
    ['pie', pieChartBlock, 'path', 4],
  ])('renders a %s chart as inline SVG with tooltips and a data table', (_kind, block, mark, count) => {
    renderBlock(block);
    const chart = screen.getByRole('region', { name: `Chart: ${block.title}` });
    const svg = within(chart).getByRole('img');
    expect(svg.querySelectorAll(mark)).toHaveLength(count);
    expect(svg.querySelector(`${mark} title`)?.textContent).toMatch(/: \d+/);
    // The numbers are also available as a (screen-reader) table.
    expect(within(chart).getByRole('table', { hidden: true })).toBeInTheDocument();
  });

  it('names two or more series in a legend', () => {
    renderBlock(barChartBlock);
    const chart = screen.getByRole('region', { name: 'Chart: Mail this week' });
    expect(within(chart).getAllByText('Important').length).toBeGreaterThan(0);
    expect(within(chart).getAllByText('Digest').length).toBeGreaterThan(0);
  });

  it('renders a read-only checklist', () => {
    renderBlock(checklistBlock);
    const list = screen.getByRole('region', { name: 'Checklist: Before the trip' });
    expect(within(list).getByText('Pay invoice')).toHaveClass('line-through');
    expect(within(list).getByText('(open)')).toBeInTheDocument();
    expect(within(list).queryByRole('checkbox')).toBeNull();
  });

  it('renders key/value facts', () => {
    renderBlock(keyValueBlock);
    const details = screen.getByRole('region', { name: 'Details: Inbox' });
    expect(within(details).getByText('Unread').nextSibling?.textContent).toBe('4');
    expect(within(details).getByText('IDLE connected').nextSibling?.textContent).toBe('Yes');
  });

  it('sends a chat message for an action button once, and runs nothing itself', async () => {
    const onAction = vi.fn(() => true);
    renderBlock(actionsBlock, onAction);
    const snooze = screen.getByRole('button', { name: 'Snooze' });
    expect(snooze).toHaveAttribute('title', 'Asks Janus to run snooze_topic with {"topic_id":4}');
    await userEvent.click(snooze);
    expect(onAction).toHaveBeenCalledWith('Run "Snooze": call snooze_topic with {"topic_id":4}');
    expect(snooze).toBeDisabled();
    await userEvent.click(snooze);
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Create rule' })).toBeEnabled();
  });

  it('keeps a button usable when its message could not be sent, and disables buttons without a session', async () => {
    const onAction = vi.fn(() => false);
    const { unmount } = renderBlock(actionsBlock, onAction);
    await userEvent.click(screen.getByRole('button', { name: 'Snooze' }));
    expect(screen.getByRole('button', { name: 'Snooze' })).toBeEnabled();
    unmount();
    renderBlock(actionsBlock);
    expect(screen.getByRole('button', { name: 'Snooze' })).toBeDisabled();
  });

  it('renders a same-origin image, and only alt text otherwise', () => {
    const { unmount } = renderBlock(imageBlock);
    expect(screen.getByRole('img', { name: 'A pencil sketch of a lighthouse' })).toHaveAttribute('src', '/api/images/abc123');
    expect(screen.getByText('Generated for you')).toBeInTheDocument();
    // JAN-253: a download link to the same authenticated URL.
    const download = screen.getByRole('link', { name: 'Download' });
    expect(download).toHaveAttribute('href', '/api/images/abc123');
    expect(download).toHaveAttribute('download');
    unmount();
    renderBlock({ ...imageBlock, url: undefined });
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.queryByRole('link', { name: 'Download' })).toBeNull();
    expect(screen.getByText('Image unavailable: A pencil sketch of a lighthouse')).toBeInTheDocument();
  });

  it('drops the download link and shows the alt text when the image fails to load', () => {
    renderBlock(imageBlock);
    fireEvent.error(screen.getByRole('img', { name: 'A pencil sketch of a lighthouse' }));
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.queryByRole('link', { name: 'Download' })).toBeNull();
    expect(screen.getByText('Image unavailable: A pencil sketch of a lighthouse')).toBeInTheDocument();
  });

  it.each(Object.entries(invalidBlocks))('falls back to JSON for an invalid block (%s)', (_name, block) => {
    renderBlock(block);
    expectFallback(block);
    expect(screen.queryByRole('link')).toBeNull();
    expect(document.querySelector('img, iframe, script')).toBeNull();
  });

  it('falls back to JSON for a block over the 8 KB budget and for non-objects', () => {
    const huge = { ...cardBlock, body: 'x'.repeat(UI_BLOCK_MAX_BYTES) };
    expect(parseUIBlock(huge)).toBeNull();
    expect(parseUIBlock('card')).toBeNull();
    expect(parseUIBlock(null)).toBeNull();
    renderBlock(42);
    expectFallback(42);
  });

  it('mirrors the server budgets', () => {
    const rows = Array.from({ length: 51 }, () => ['a', 'b', false]);
    expect(parseUIBlock({ ...tableBlock, rows })).toBeNull();
    expect(parseUIBlock({ ...tableBlock, rows: rows.slice(0, 50) })).not.toBeNull();
    expect(parseUIBlock({ ...tableBlock, columns: Array.from({ length: 9 }, (_, i) => `c${i}`), rows: [] })).toBeNull();
    const series = Array.from({ length: 6 }, (_, i) => ({ name: `s${i}`, values: [1, 2, 3, 4, 5] }));
    expect(parseUIBlock({ ...barChartBlock, series })).toBeNull();
    const buttons = Array.from({ length: 7 }, () => actionsBlock.buttons[0]);
    expect(parseUIBlock({ ...actionsBlock, buttons })).toBeNull();
    const items = Array.from({ length: 51 }, () => ({ text: 'x', checked: false }));
    expect(parseUIBlock({ ...checklistBlock, items })).toBeNull();
    expect(parseUIBlock({ ...pieChartBlock, series: [{ name: 'a', values: [1, -1, 1, 1] }] })).toBeNull();
    expect(parseUIBlock({ ...cardBlock, links: [{ label: 'x', href: '//evil.example.com' }] })).toBeNull();
  });

  it('keeps rendering the other blocks of a reply when one is invalid', () => {
    render(
      <MemoryRouter>
        <UIBlockList blocks={[cardBlock, invalidBlocks.unknownType, tableBlock]} />
      </MemoryRouter>
    );
    expect(screen.getByRole('region', { name: 'Card' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Unrenderable block' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Table: Open tasks' })).toBeInTheDocument();
  });

  it('reads stored rows by their payload and words action messages', () => {
    expect(toUIBlockPayloads([{ block_id: 'b', payload: cardBlock }, { block_id: 'c' }, null])).toEqual([cardBlock]);
    expect(toUIBlockPayloads(undefined)).toEqual([]);
    expect(actionMessage({ label: 'Go', tool: 'x', args: {}, style: 'secondary' })).toBe('Run "Go": call x');
  });
});
