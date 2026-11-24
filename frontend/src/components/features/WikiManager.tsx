import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { BookOpen, List, Share2 } from 'lucide-react';
import { wikiApi } from '../../api';
import type { WikiPageSummary, WikiSynthesisStatus } from '../../types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';
import { Input } from '../ui/Input';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import { KnowledgeGraphView } from './KnowledgeGraphView';
import { WikiPageView } from './WikiPageView';
import { useWikiSelection } from './wikiLinkTarget';

const cloudWarning = (model: string) =>
  `Wiki synthesis sends the passages and memory facts behind your topics and entities (from mail, chats, calendar, tasks, and ` +
  `documents) to ${model}. Turn it on?`;

/** The Wiki synthesis switch (JAN-244); it shares the knowledge budget shown on the Knowledge tab. */
const SynthesisCard: React.FC = () => {
  const queryClient = useQueryClient();
  const { confirm, dialog } = useConfirmDialog();
  const { data: status, isError } = useQuery<WikiSynthesisStatus>({
    queryKey: ['wiki-synthesis'],
    queryFn: async () => (await wikiApi.getSynthesis()).data,
  });
  const mutation = useMutation({
    mutationFn: (enabled: boolean) => wikiApi.updateSynthesis(enabled),
    onSuccess: (response) => queryClient.setQueryData(['wiki-synthesis'], response.data),
  });
  if (isError) {
    return (
      <Card className="p-6">
        <p className="text-sm text-red-600 dark:text-red-400" role="alert">
          Could not load the wiki settings.
        </p>
      </Card>
    );
  }
  if (!status) return null;
  return (
    <Card className="p-6 flex flex-col md:flex-row md:items-center justify-between gap-4">
      <div>
        <h3 className="text-lg font-bold">Wiki synthesis</h3>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Every hour, {status.model} updates the pages whose sources changed, within the monthly knowledge budget (
          {status.tokens_used.toLocaleString('en-US')} of {status.monthly_token_budget.toLocaleString('en-US')} tokens
          used in {status.period}). Pages you edited get proposals instead of rewrites.
        </p>
        {status.is_cloud && (
          <p className="mt-2 text-xs rounded-xl p-3 bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-300">
            {status.model} is a cloud model: with synthesis on, the passages and memory facts behind your pages go to that provider.
          </p>
        )}
      </div>
      <label className="flex items-center gap-2 text-sm font-medium shrink-0">
        <input
          type="checkbox"
          aria-label="Wiki synthesis"
          checked={status.enabled}
          disabled={mutation.isPending}
          onChange={(event) => {
            const enabled = event.target.checked;
            if (enabled && status.is_cloud) {
              confirm({
                title: 'Send passages to a cloud model?',
                message: cloudWarning(status.model),
                confirmLabel: 'Turn on',
                tone: 'warning',
                onConfirm: () => mutation.mutate(enabled),
              });
              return;
            }
            mutation.mutate(enabled);
          }}
        />
        {status.enabled ? 'On' : 'Off'}
      </label>
      {dialog}
    </Card>
  );
};

type PageListProps = {
  query: string;
  onQueryChange: (value: string) => void;
  current: number | null;
  onPick: (id: number) => void;
};

/** The searchable page list on the left of the Wiki tab. */
const PageList: React.FC<PageListProps> = ({ query, onQueryChange, current, onPick }) => {
  const { data: pages = [], isLoading, isError } = useQuery<WikiPageSummary[]>({
    queryKey: ['wiki-pages', query],
    queryFn: async () => (await wikiApi.listPages({ query })).data,
  });
  return (
    <Card className="p-4 space-y-3 self-start">
      <Input label="Search pages" value={query} onChange={(event) => onQueryChange(event.target.value)} />
      {isLoading ? (
        <p className="text-sm text-gray-500 dark:text-gray-400">Loading pages…</p>
      ) : isError ? (
        <p className="text-sm text-red-600 dark:text-red-400" role="alert">
          Could not load the pages.
        </p>
      ) : pages.length === 0 ? (
        <p className="text-sm text-gray-500 dark:text-gray-400">
          {query.trim()
            ? 'No pages match this search.'
            : 'No pages yet. Turn on wiki synthesis; pages appear for active topics and busy entities.'}
        </p>
      ) : (
        <ul className="space-y-1" aria-label="Wiki pages">
          {pages.map((page) => (
            <li key={page.id}>
              <button
                type="button"
                onClick={() => onPick(page.id)}
                aria-current={current === page.id ? 'page' : undefined}
                className={`w-full text-left rounded-xl px-3 py-2 text-sm hover:bg-gray-50 dark:hover:bg-gray-800 ${
                  current === page.id ? 'bg-gray-100 dark:bg-gray-800 font-semibold' : ''
                }`}
              >
                <span className="block truncate">{page.title}</span>
                <span className="flex gap-1 mt-1">
                  <Badge variant="gray">{page.subject_type === 'topic' ? 'Topic' : 'Entity'}</Badge>
                  {page.pending_proposals > 0 && (
                    <Badge variant="warning">
                      {page.pending_proposals} proposal{page.pending_proposals === 1 ? '' : 's'}
                    </Badge>
                  )}
                  {!page.synthesized_at && !page.user_edited && <Badge variant="gray">not written</Badge>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
};

/** Switches the Wiki tab between the page list and the knowledge graph (JAN-334). */
const ViewSwitch: React.FC<{ graph: boolean; onChange: (graph: boolean) => void }> = ({ graph, onChange }) => (
  <div className="flex gap-2" role="group" aria-label="Wiki view">
    <Button type="button" variant={graph ? 'ghost' : 'secondary'} icon={<List size={16} />} aria-pressed={!graph} onClick={() => onChange(false)}>
      Pages
    </Button>
    <Button type="button" variant={graph ? 'secondary' : 'ghost'} icon={<Share2 size={16} />} aria-pressed={graph} onClick={() => onChange(true)}>
      Graph
    </Button>
  </div>
);

/**
 * The Wiki tab (JAN-244): synthesis switch, page list with search, and the open page (or `?wiki=` link target);
 * the Graph view (JAN-334) shows how topics and entities connect and opens their pages.
 */
export const WikiManager: React.FC = () => {
  const [searchParams] = useSearchParams();
  const [graph, setGraph] = useState(false);
  const topicParam = searchParams.get('topic');
  const wiki = useWikiSelection();
  const { data: topicPages } = useQuery<WikiPageSummary[]>({
    queryKey: ['wiki-pages', 'topic', topicParam],
    queryFn: async () => (await wikiApi.listPages({ subjectType: 'topic', subjectId: Number(topicParam) })).data,
    enabled: topicParam !== null && !Number.isNaN(Number(topicParam)),
  });
  // A wiki link (?wiki=<title>) or topic link (?topic=<id>) opens its page until the user picks another one.
  const current = wiki.pickedId ?? wiki.linkedPageId ?? topicPages?.[0]?.id ?? null;
  const openPage = (id: number) => {
    wiki.pick(id);
    setGraph(false);
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold tracking-tight flex items-center gap-3">
          <BookOpen className="text-primary" />
          Wiki
        </h2>
        <p className="text-gray-500 dark:text-gray-400 mt-1">
          A page for each tracked topic and each person, organization, or project that comes up often, written from
          your sources with citations.
        </p>
      </div>
      <SynthesisCard />
      <ViewSwitch graph={graph} onChange={setGraph} />
      {topicParam !== null && topicPages && topicPages.length === 0 && (
        <p className="text-sm text-gray-500 dark:text-gray-400">
          This topic has no wiki page yet; it gets one on the next synthesis run while the topic is active or pinned.
        </p>
      )}
      {wiki.missingName !== null && (
        <p className="text-sm text-gray-500 dark:text-gray-400" role="status">
          No wiki page called “{wiki.missingName}” yet.
        </p>
      )}
      {graph ? (
        <KnowledgeGraphView onOpenPage={openPage} />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
          <PageList query={wiki.query} onQueryChange={wiki.setQuery} current={current} onPick={wiki.pick} />
          {current !== null ? (
            <WikiPageView key={current} pageId={current} onOpenPage={openPage} />
          ) : (
            <Card className="p-6 text-sm text-gray-500 dark:text-gray-400">Choose a page.</Card>
          )}
        </div>
      )}
    </div>
  );
};
