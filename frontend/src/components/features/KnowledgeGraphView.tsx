import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BookOpen } from 'lucide-react';
import { layoutGraph } from '../../lib/graphLayout';
import { knowledgeStackApi, type KnowledgeGraph, type KnowledgeGraphNode } from '../../lib/knowledgeStack';
import { VIZ_STYLE } from './stats/DailyBarChart';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';

const WIDTH = 800;
const HEIGHT = 520;

/** Four categorical slots from the shared `--viz-*` palette; the legend names each, so color is never alone. */
const CATEGORIES = [
  { key: 'topic', label: 'Topic', color: 'var(--viz-1)' },
  { key: 'person', label: 'Person', color: 'var(--viz-2)' },
  { key: 'organization', label: 'Organization', color: 'var(--viz-3)' },
  { key: 'other', label: 'Project, place, product, or concept', color: 'var(--viz-4)' },
] as const;

const categoryOf = (node: KnowledgeGraphNode) =>
  node.kind === 'topic' ? 'topic' : node.entity_type === 'person' || node.entity_type === 'organization' ? node.entity_type : 'other';

const colorOf = (node: KnowledgeGraphNode) => CATEGORIES.find((item) => item.key === categoryOf(node))!.color;

const radiusOf = (node: KnowledgeGraphNode) => (node.kind === 'topic' ? 9 : 5 + Math.min(Math.sqrt(node.mentions), 6));

const shortLabel = (label: string) => (label.length > 22 ? `${label.slice(0, 21)}…` : label);

type Neighbour = { node: KnowledgeGraphNode; kind: string; label?: string | null; weight: number };

const neighboursOf = (graph: KnowledgeGraph, nodeId: string, byId: Map<string, KnowledgeGraphNode>): Neighbour[] =>
  graph.edges
    .filter((edge) => edge.source === nodeId || edge.target === nodeId)
    .map((edge) => ({
      node: byId.get(edge.source === nodeId ? edge.target : edge.source)!,
      kind: edge.kind,
      label: edge.label,
      weight: edge.weight,
    }))
    .filter((item) => item.node);

const connectionText = (item: Neighbour) =>
  item.kind === 'relation'
    ? (item.label ?? 'related').replace(/_/g, ' ')
    : `${item.weight} shared passage${item.weight === 1 ? '' : 's'}`;

type DetailsProps = {
  node: KnowledgeGraphNode;
  neighbours: Neighbour[];
  onSelect: (id: string) => void;
  onOpenPage?: (pageId: number) => void;
};

const NodeDetails: React.FC<DetailsProps> = ({ node, neighbours, onSelect, onOpenPage }) => (
  <div className="space-y-2" aria-label="Selected node">
    <div className="flex flex-wrap items-center gap-2">
      <h4 className="text-sm font-bold">{node.label}</h4>
      <Badge variant="gray">{node.kind === 'topic' ? 'Topic' : node.entity_type}</Badge>
      {node.wiki_page_id && onOpenPage && (
        <Button type="button" variant="ghost" icon={<BookOpen size={14} />} onClick={() => onOpenPage(node.wiki_page_id!)}>
          Open wiki page
        </Button>
      )}
    </div>
    {neighbours.length === 0 ? (
      <p className="text-sm text-gray-500 dark:text-gray-400">No links to the other nodes shown.</p>
    ) : (
      <ul className="text-sm space-y-1" aria-label={`Connections of ${node.label}`}>
        {neighbours.map((item, index) => (
          // A pair can be linked both ways with the same predicate, so the position keeps keys unique.
          <li key={`${index}-${item.kind}-${item.node.id}-${item.label ?? ''}`}>
            <button type="button" className="text-primary hover:underline" onClick={() => onSelect(item.node.id)}>
              {item.node.label}
            </button>{' '}
            <span className="text-gray-500 dark:text-gray-400">· {connectionText(item)}</span>
          </li>
        ))}
      </ul>
    )}
  </div>
);

type GraphSvgProps = {
  graph: KnowledgeGraph;
  selected: string | null;
  onSelect: (id: string) => void;
};

const GraphSvg: React.FC<GraphSvgProps> = ({ graph, selected, onSelect }) => {
  const positions = useMemo(
    () => layoutGraph(graph.nodes.map((node) => node.id), graph.edges, { width: WIDTH, height: HEIGHT }),
    [graph],
  );
  const linked = new Set(
    selected
      ? graph.edges.flatMap((edge) => (edge.source === selected || edge.target === selected ? [edge.source, edge.target] : []))
      : [],
  );
  const dimmed = (id: string) => selected !== null && id !== selected && !linked.has(id);
  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      className="w-full h-auto text-gray-400 dark:text-gray-500"
      role="group"
      aria-label="Knowledge graph"
    >
      {graph.edges.map((edge) => {
        const from = positions.get(edge.source);
        const to = positions.get(edge.target);
        if (!from || !to) return null;
        const active = selected !== null && (edge.source === selected || edge.target === selected);
        return (
          <line
            key={`${edge.kind}-${edge.source}-${edge.target}-${edge.label ?? ''}`}
            x1={from.x}
            y1={from.y}
            x2={to.x}
            y2={to.y}
            stroke="currentColor"
            strokeWidth={active ? 2 : Math.min(1 + edge.weight / 3, 3)}
            strokeDasharray={edge.kind === 'relation' ? '4 3' : undefined}
            opacity={selected === null ? 0.55 : active ? 0.95 : 0.12}
          >
            <title>{edge.kind === 'relation' ? (edge.label ?? 'related').replace(/_/g, ' ') : `${edge.weight} shared passages`}</title>
          </line>
        );
      })}
      {graph.nodes.map((node) => {
        const point = positions.get(node.id)!;
        const radius = radiusOf(node);
        return (
          <g
            key={node.id}
            role="button"
            tabIndex={0}
            aria-label={`${node.kind === 'topic' ? 'Topic' : node.entity_type}: ${node.label}`}
            aria-pressed={selected === node.id}
            className="cursor-pointer focus:outline-none"
            opacity={dimmed(node.id) ? 0.25 : 1}
            onClick={() => onSelect(node.id)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onSelect(node.id);
              }
            }}
          >
            {node.kind === 'topic' ? (
              <rect x={point.x - radius} y={point.y - radius} width={radius * 2} height={radius * 2} rx={3} fill={colorOf(node)} />
            ) : (
              <circle cx={point.x} cy={point.y} r={radius} fill={colorOf(node)} />
            )}
            {selected === node.id && (
              <circle cx={point.x} cy={point.y} r={radius + 4} fill="none" stroke="currentColor" strokeWidth={2} className="text-primary" />
            )}
            <text
              x={point.x}
              y={point.y + radius + 12}
              textAnchor="middle"
              fontSize={11}
              fill="currentColor"
              className="text-gray-700 dark:text-gray-200 select-none"
            >
              {shortLabel(node.label)}
            </text>
          </g>
        );
      })}
    </svg>
  );
};

const GraphTable: React.FC<{ graph: KnowledgeGraph; byId: Map<string, KnowledgeGraphNode> }> = ({ graph, byId }) => (
  <table className="w-full text-sm" aria-label="Knowledge graph connections">
    <thead>
      <tr className="text-left text-xs text-gray-400 uppercase">
        <th className="py-1 pr-2">From</th>
        <th className="py-1 pr-2">Link</th>
        <th className="py-1">To</th>
      </tr>
    </thead>
    <tbody>
      {graph.edges.map((edge) => (
        <tr key={`${edge.kind}-${edge.source}-${edge.target}-${edge.label ?? ''}`} className="border-t border-gray-100 dark:border-gray-800">
          <td className="py-1 pr-2">{byId.get(edge.source)?.label}</td>
          <td className="py-1 pr-2 text-gray-500 dark:text-gray-400">
            {connectionText({ node: byId.get(edge.target)!, kind: edge.kind, label: edge.label, weight: edge.weight })}
          </td>
          <td className="py-1">{byId.get(edge.target)?.label}</td>
        </tr>
      ))}
    </tbody>
  </table>
);

/**
 * The knowledge graph (JAN-334): active and pinned topics, the entities their sources mention, and the
 * relations extracted between entities. Selecting a node highlights its links, lists them, and opens its
 * wiki page when it has one. A table view lists every link for screen readers and exact reading.
 */
export const KnowledgeGraphView: React.FC<{ onOpenPage?: (pageId: number) => void }> = ({ onOpenPage }) => {
  const [selected, setSelected] = useState<string | null>(null);
  const [showTable, setShowTable] = useState(false);
  const { data: graph, isLoading, isError } = useQuery<KnowledgeGraph>({
    queryKey: ['knowledge-graph'],
    queryFn: async () => (await knowledgeStackApi.getGraph()).data,
  });
  const byId = useMemo(() => new Map((graph?.nodes ?? []).map((node) => [node.id, node])), [graph]);
  if (isLoading) return <Card className="p-6 text-sm text-gray-500 dark:text-gray-400">Loading the graph…</Card>;
  if (isError || !graph) {
    return (
      <Card className="p-6">
        <p className="text-sm text-red-600 dark:text-red-400" role="alert">
          Could not load the knowledge graph.
        </p>
      </Card>
    );
  }
  if (graph.nodes.length === 0) {
    return (
      <Card className="p-6 text-sm text-gray-500 dark:text-gray-400">
        Nothing to show yet. Topics appear once they are active or pinned; entities once knowledge extraction has
        found them in your sources.
      </Card>
    );
  }
  const current = selected ? byId.get(selected) : undefined;
  return (
    <Card className="p-6 space-y-4 stats-viz" aria-label="Knowledge graph view">
      <style>{VIZ_STYLE}</style>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600 dark:text-gray-300" aria-label="Legend">
          {CATEGORIES.map((item) => (
            <li key={item.key} className="flex items-center gap-1">
              <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: item.color }} aria-hidden />
              {item.label}
            </li>
          ))}
          <li>— evidence · - - relation</li>
        </ul>
        <button type="button" className="text-xs font-medium text-primary hover:underline" onClick={() => setShowTable((value) => !value)}>
          {showTable ? 'Show graph' : 'Show table'}
        </button>
      </div>
      {showTable ? (
        <GraphTable graph={graph} byId={byId} />
      ) : (
        <GraphSvg graph={graph} selected={selected} onSelect={(id) => setSelected((value) => (value === id ? null : id))} />
      )}
      {current ? (
        <NodeDetails node={current} neighbours={neighboursOf(graph, current.id, byId)} onSelect={setSelected} onOpenPage={onOpenPage} />
      ) : (
        <p className="text-sm text-gray-500 dark:text-gray-400">Select a topic or entity to see its links.</p>
      )}
    </Card>
  );
};
