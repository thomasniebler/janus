import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import MemoryPage from './MemoryPage';

vi.mock('../components/features/MemoryManager', () => ({
  MemoryManager: () => <div>Mock memory manager</div>,
}));

vi.mock('../components/features/TopicManager', () => ({
  TopicManager: () => <div>Mock topic manager</div>,
}));

vi.mock('../components/features/KnowledgeGraphManager', () => ({
  KnowledgeGraphManager: () => <div>Mock knowledge graph</div>,
}));

vi.mock('../components/features/WikiManager', () => ({
  WikiManager: () => <div>Mock wiki</div>,
}));

vi.mock('../components/features/KnowledgeSourcesManager', () => ({
  KnowledgeSourcesManager: () => <div>Mock knowledge sources</div>,
}));

vi.mock('../components/features/EmailClarificationQueue', () => ({
  EmailClarificationQueue: () => <div>Mock clarification queue</div>,
}));

vi.mock('../components/features/RuleManager', () => ({
  RuleManager: () => <div>Mock rule manager</div>,
}));

const renderPage = (initialEntry: string = '/memory') =>
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <MemoryPage />
    </MemoryRouter>
  );

describe('MemoryPage', () => {
  it('renders the topics section when selected from the section nav', async () => {
    renderPage();
    const user = userEvent.setup();

    expect(screen.getByText('Mock memory manager')).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: /topics/i }));

    expect(screen.getByText('Mock topic manager')).toBeInTheDocument();
    expect(screen.queryByText('Mock memory manager')).not.toBeInTheDocument();
  });

  it('restores the topics section from the query string', () => {
    renderPage('/memory?tab=topics');

    expect(screen.getByText('Mock topic manager')).toBeInTheDocument();
    expect(screen.queryByText('Mock memory manager')).not.toBeInTheDocument();
  });

  it('opens the documents section from the section nav', async () => {
    renderPage();
    await userEvent.setup().click(screen.getByRole('tab', { name: /documents/i }));
    expect(screen.getByText('Mock knowledge sources')).toBeInTheDocument();
  });

  it('restores the documents section from the query string', () => {
    renderPage('/memory?tab=documents');
    expect(screen.getByText('Mock knowledge sources')).toBeInTheDocument();
  });

  it('opens and restores the knowledge section', async () => {
    renderPage('/memory?tab=knowledge');
    expect(screen.getByText('Mock knowledge graph')).toBeInTheDocument();
  });

  it('restores the wiki section from the query string', () => {
    renderPage('/memory?tab=wiki');
    expect(screen.getByText('Mock wiki')).toBeInTheDocument();
  });
});
