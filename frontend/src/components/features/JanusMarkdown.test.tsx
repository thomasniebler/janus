import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { JanusMarkdown } from './JanusMarkdown';

const Where = () => {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
};

const reply = 'See [[Other Page]] or [docs](https://example.com/docs); write `[[code]]` to link.';

describe('JanusMarkdown', () => {
  it('routes [[wiki links]] in-app and keeps other links as they were', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <Routes>
          <Route path="/chat" element={<JanusMarkdown>{reply}</JanusMarkdown>} />
          <Route path="/memory" element={<Where />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByRole('link', { name: 'docs' })).toHaveAttribute('href', 'https://example.com/docs');
    expect(screen.getByText('[[code]]').tagName).toBe('CODE');
    await userEvent.click(screen.getByRole('link', { name: 'Other Page' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/memory?tab=wiki&wiki=Other%20Page');
  });

  it('renders a hostile label as text, never as markup', () => {
    const { container } = render(
      <MemoryRouter>
        <JanusMarkdown>{'[[Page|<img src=x onerror=alert(1)> **b** https://evil.example &lt;]]'}</JanusMarkdown>
      </MemoryRouter>,
    );
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('strong')).toBeNull();
    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveTextContent('<img src=x onerror=alert(1)> **b** https://evil.example &lt;');
    expect(links[0]).toHaveAttribute('href', '/memory?tab=wiki&wiki=Page');
  });

  it('falls back to a plain anchor outside a router', () => {
    render(<JanusMarkdown>{'[[Other Page]]'}</JanusMarkdown>);
    expect(screen.getByRole('link', { name: 'Other Page' })).toHaveAttribute(
      'href',
      '/memory?tab=wiki&wiki=Other%20Page',
    );
  });
});
