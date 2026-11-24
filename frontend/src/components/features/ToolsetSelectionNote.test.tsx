import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { ToolsetSelection } from '../../contexts/chatSession';
import { ToolsetSelectionNote } from './ToolsetSelectionNote';

const selected: ToolsetSelection = {
  source: 'selected',
  reason: 'Topics question.',
  tool_ids: ['core', 'topics', 'docs'],
  trimmed: ['mail.send'],
  widened: ['memory'],
  authorized_count: 20,
  narrowed: true,
};

describe('ToolsetSelectionNote', () => {
  it('names the picked tools, the reason, widenings and trimmed ids', () => {
    render(<ToolsetSelectionNote toolset={selected} />);
    const note = screen.getByRole('note', { name: 'Tool selection' });
    expect(note).toHaveTextContent('Tools picked for this message (3 of 20): core, topics, docs');
    expect(note).toHaveTextContent('Why: Topics question.');
    expect(note).toHaveTextContent('Added on request: memory');
    expect(note).toHaveTextContent('Not available here, dropped: mail.send');
  });

  it('renders nothing when nothing was narrowed', () => {
    const { container } = render(
      <ToolsetSelectionNote toolset={{ ...selected, source: 'fallback', tool_ids: null, narrowed: false }} />,
    );
    expect(container).toBeEmptyDOMElement();
    const { container: empty } = render(<ToolsetSelectionNote toolset={null} />);
    expect(empty).toBeEmptyDOMElement();
  });
});
