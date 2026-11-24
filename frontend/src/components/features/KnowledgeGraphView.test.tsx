import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { knowledgeStackApi } from '../../lib/knowledgeStack';
import { KnowledgeGraphView } from './KnowledgeGraphView';
import { sampleGraph } from './knowledgeStackFixtures';

const renderGraph = (onOpenPage = vi.fn()) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <KnowledgeGraphView onOpenPage={onOpenPage} />
    </QueryClientProvider>,
  );
  return onOpenPage;
};

describe('KnowledgeGraphView', () => {
  afterEach(() => vi.restoreAllMocks());

  it('draws topics and entities, lists a selected node’s links, and opens its wiki page', async () => {
    vi.spyOn(knowledgeStackApi, 'getGraph').mockResolvedValue({ data: sampleGraph() } as never);
    const onOpenPage = renderGraph();
    const graph = await screen.findByRole('group', { name: 'Knowledge graph' });
    expect(within(graph).getAllByRole('button')).toHaveLength(6);

    await userEvent.click(within(graph).getByRole('button', { name: 'person: Dana Whitfield' }));
    const links = screen.getByRole('list', { name: 'Connections of Dana Whitfield' });
    expect(links).toHaveTextContent('Acme renewal');
    expect(links).toHaveTextContent('works for');

    await userEvent.click(within(links).getByRole('button', { name: 'Northwind' }));
    expect(screen.getByRole('list', { name: 'Connections of Northwind' })).toHaveTextContent('Apollo migration');
    expect(screen.queryByRole('button', { name: 'Open wiki page' })).not.toBeInTheDocument();

    await userEvent.click(within(graph).getByRole('button', { name: 'Topic: Acme renewal' }));
    await userEvent.click(screen.getByRole('button', { name: 'Open wiki page' }));
    expect(onOpenPage).toHaveBeenCalledWith(1);
  });

  it('selects nodes from the keyboard and offers a table of every link', async () => {
    vi.spyOn(knowledgeStackApi, 'getGraph').mockResolvedValue({ data: sampleGraph() } as never);
    renderGraph();
    const node = await screen.findByRole('button', { name: 'project: Apollo' });
    node.focus();
    await userEvent.keyboard('{Enter}');
    expect(node).toHaveAttribute('aria-pressed', 'true');

    await userEvent.click(screen.getByRole('button', { name: 'Show table' }));
    const table = screen.getByRole('table', { name: 'Knowledge graph connections' });
    expect(within(table).getAllByRole('row')).toHaveLength(6);
    expect(table).toHaveTextContent('3 shared passages');
  });

  it('lists both directions of a symmetric relation without duplicate keys', async () => {
    const graph = sampleGraph();
    const [first] = graph.edges.filter((edge) => edge.kind === 'relation');
    graph.edges.push({ ...first, source: first.target, target: first.source });
    vi.spyOn(knowledgeStackApi, 'getGraph').mockResolvedValue({ data: graph } as never);
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    renderGraph();
    const sourceLabel = graph.nodes.find((item) => item.id === first.source)!.label;
    const targetLabel = graph.nodes.find((item) => item.id === first.target)!.label;
    const view = await screen.findByRole('group', { name: 'Knowledge graph' });
    await userEvent.click(within(view).getByRole('button', { name: new RegExp(`: ${sourceLabel}$`) }));
    const links = screen.getByRole('list', { name: `Connections of ${sourceLabel}` });
    expect(within(links).getAllByRole('button', { name: targetLabel })).toHaveLength(2);
    expect(consoleError.mock.calls.flat().join(' ')).not.toMatch(/same key/);
  });

  it('explains an empty graph and shows load failures', async () => {
    const spy = vi.spyOn(knowledgeStackApi, 'getGraph').mockResolvedValue({ data: { nodes: [], edges: [] } } as never);
    renderGraph();
    expect(await screen.findByText(/Nothing to show yet/)).toBeInTheDocument();

    spy.mockRejectedValue(new Error('down'));
    renderGraph();
    expect(await screen.findByText('Could not load the knowledge graph.')).toBeInTheDocument();
  });
});
