import { formatServerDate, formatServerDateTime } from '../../lib/serverTime';
import React, { useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, History, Pencil, RotateCcw, X } from 'lucide-react';
import { wikiApi } from '../../api';
import type { WikiCitation, WikiPageDetail, WikiRevision } from '../../types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';
import { JanusMarkdown } from './JanusMarkdown';
import { WikiProvenance } from './WikiProvenance';

const AUTHOR_LABELS: Record<WikiRevision['author'], string> = { janus: 'Janus', user: 'You', agent: 'Janus (chat)' };

const formatDate = (value?: string | null) => formatServerDateTime(value) ?? '';

/**
 * Turns `[n]` markers into links to the page's source list. It runs before the `[[Page]]` wiki-link
 * transform, which leaves the resulting `[[n]](#wiki-cite-n)` links alone (numeric target, explicit link).
 */
const linkCitations = (body: string, citations: WikiCitation[]) => {
  const known = new Set(citations.map((citation) => citation.n));
  return body.replace(/\[(\d+)\]/g, (marker, number) =>
    known.has(Number(number)) ? `[[${number}]](#wiki-cite-${number})` : marker,
  );
};

const errorMessage = (error: unknown) =>
  axios.isAxiosError(error) ? (error.response?.data?.detail ?? error.message) : 'Saving failed.';

/** The `[n]` a click landed on, when it hit a citation marker link. */
const citationClicked = (event: React.MouseEvent) => {
  const link = (event.target as HTMLElement).closest?.('a[href^="#wiki-cite-"]');
  return link ? Number(link.getAttribute('href')!.slice('#wiki-cite-'.length)) : null;
};

type PageTextProps = { body: string; citations: WikiCitation[]; onCite?: (n: number) => void };

/** Page text; with `onCite`, a `[n]` marker drills down to that source instead of jumping to the list (JAN-334). */
const PageText: React.FC<PageTextProps> = ({ body, citations, onCite }) => (
  <div
    className="prose prose-sm dark:prose-invert max-w-none"
    onClickCapture={(event) => {
      const n = onCite ? citationClicked(event) : null;
      if (n === null || !onCite) return;
      event.preventDefault();
      event.stopPropagation();
      onCite(n);
    }}
  >
    <JanusMarkdown>{linkCitations(body, citations)}</JanusMarkdown>
  </div>
);

const Sources: React.FC<{ citations: WikiCitation[] }> = ({ citations }) =>
  citations.length === 0 ? null : (
    <ol className="space-y-1 text-xs text-gray-500 dark:text-gray-400" aria-label="Sources">
      {citations.map((citation) => (
        <li key={citation.n} id={`wiki-cite-${citation.n}`}>
          [{citation.n}] {citation.title || '(untitled)'}
          {citation.source_type ? ` · ${citation.source_type}` : ''}
          {citation.date ? ` · ${formatServerDate(citation.date) ?? citation.date}` : ''}
        </li>
      ))}
    </ol>
  );

type WikiPageTab = 'page' | 'sources';

const TABS: { id: WikiPageTab; label: string }[] = [
  { id: 'page', label: 'Page' },
  { id: 'sources', label: 'Sources' },
];

/** The Page / Sources switch of a wiki page (JAN-334); arrow keys move between the tabs. */
const WikiPageTabs: React.FC<{ pageId: number; current: WikiPageTab; onChange: (tab: WikiPageTab) => void }> = ({
  pageId,
  current,
  onChange,
}) => (
  <div role="tablist" aria-label="Page views" className="flex gap-1 border-b border-gray-200 dark:border-gray-700">
    {TABS.map((tab) => (
      <button
        key={tab.id}
        type="button"
        role="tab"
        id={`wiki-tab-${pageId}-${tab.id}`}
        aria-selected={current === tab.id}
        aria-controls={`wiki-tabpanel-${pageId}-${tab.id}`}
        tabIndex={current === tab.id ? 0 : -1}
        onClick={() => onChange(tab.id)}
        onKeyDown={(event) => {
          if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
          const next = TABS[(TABS.findIndex((item) => item.id === current) + 1) % TABS.length];
          onChange(next.id);
          document.getElementById(`wiki-tab-${pageId}-${next.id}`)?.focus();
        }}
        className={`px-3 py-2 text-sm font-medium -mb-px border-b-2 ${
          current === tab.id
            ? 'border-primary text-primary'
            : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
        }`}
      >
        {tab.label}
      </button>
    ))}
  </div>
);

type ProposalProps = {
  page: WikiPageDetail;
  proposal: WikiRevision;
  accepting: boolean;
  dismissing: boolean;
  onAccept: () => void;
  onDismiss: () => void;
};

/** A pending rewrite shown next to the current text, with Accept / Dismiss. */
const ProposalCard: React.FC<ProposalProps> = ({ page, proposal, accepting, dismissing, onAccept, onDismiss }) => (
  <div className="rounded-2xl border border-amber-200 dark:border-amber-800 p-4 space-y-3" aria-label="Proposed revision">
    <p className="text-sm font-medium">
      {AUTHOR_LABELS[proposal.author]} proposes a new version ({formatDate(proposal.created_at)})
      {proposal.note ? `: ${proposal.note}` : ''}
    </p>
    <div className="grid gap-4 md:grid-cols-2">
      <div>
        <p className="text-xs font-bold text-gray-400 uppercase mb-1">Current</p>
        <PageText body={page.body} citations={page.citations} />
      </div>
      <div>
        <p className="text-xs font-bold text-gray-400 uppercase mb-1">Proposed</p>
        <PageText body={proposal.body} citations={proposal.citations} />
      </div>
    </div>
    <div className="flex gap-2">
      <Button type="button" icon={<Check size={16} />} isLoading={accepting} onClick={onAccept}>
        Accept
      </Button>
      <Button type="button" variant="ghost" icon={<X size={16} />} isLoading={dismissing} onClick={onDismiss}>
        Dismiss
      </Button>
    </div>
  </div>
);

type EditFormProps = {
  pageId: number;
  draft: string;
  onDraft: (value: string | null) => void;
  save: { mutate: (body: string) => void; isPending: boolean; isError: boolean; error: unknown };
};

/** The Markdown editor for a user edit. */
const EditForm: React.FC<EditFormProps> = ({ pageId, draft, onDraft, save }) => (
  <form
    className="space-y-2"
    onSubmit={(event) => {
      event.preventDefault();
      save.mutate(draft);
    }}
  >
    <label className="text-xs font-bold text-gray-400 uppercase" htmlFor={`wiki-edit-${pageId}`}>
      Page text (Markdown; keep [n] to cite sources)
    </label>
    <textarea
      id={`wiki-edit-${pageId}`}
      className="w-full min-h-[16rem] rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-dark-bg p-3 text-sm font-mono"
      value={draft}
      onChange={(event) => onDraft(event.target.value)}
    />
    {save.isError && (
      <p className="text-sm text-red-600 dark:text-red-400" role="alert">
        {errorMessage(save.error)}
      </p>
    )}
    <p className="text-xs text-gray-500 dark:text-gray-400">
      Once you save, Janus proposes updates to this page instead of rewriting it.
    </p>
    <div className="flex gap-2">
      <Button type="submit" isLoading={save.isPending} disabled={!draft.trim()}>
        Save
      </Button>
      <Button type="button" variant="ghost" onClick={() => onDraft(null)}>
        Cancel
      </Button>
    </div>
  </form>
);

type HistoryProps = {
  revisions: WikiRevision[];
  restoring: number | null;
  onRestore: (id: number) => void;
};

/** Published revisions, newest first; older ones can be restored. */
const HistoryList: React.FC<HistoryProps> = ({ revisions, restoring, onRestore }) => (
  <div className="space-y-2">
    <h4 className="text-sm font-bold">History</h4>
    <ul className="space-y-1 text-sm" aria-label="Revisions">
      {revisions.map((revision, index) => (
        <li key={revision.id} className="flex items-center justify-between gap-2">
          <span>
            {AUTHOR_LABELS[revision.author]} · {formatDate(revision.created_at)}
            {revision.note ? ` · ${revision.note}` : ''}
            {index === 0 ? ' · current' : ''}
          </span>
          {index > 0 && (
            <Button
              type="button"
              variant="ghost"
              icon={<RotateCcw size={14} />}
              isLoading={restoring === revision.id}
              onClick={() => onRestore(revision.id)}
            >
              Restore
            </Button>
          )}
        </li>
      ))}
    </ul>
  </div>
);

type HeaderProps = { page: WikiPageDetail; editing: boolean; onEdit: () => void; onToggleHistory: () => void };

/** Title, badges, and the Edit / History buttons (hidden while editing). */
const PageHeader: React.FC<HeaderProps> = ({ page, editing, onEdit, onToggleHistory }) => (
  <div className="flex flex-col md:flex-row md:items-start justify-between gap-3">
    <div className="space-y-1">
      <h3 className="text-xl font-bold">{page.title}</h3>
      <div className="flex flex-wrap gap-2 text-xs text-gray-500 dark:text-gray-400">
        <Badge variant="gray">{page.subject_type === 'topic' ? 'Topic' : 'Entity'}</Badge>
        {page.user_edited && <Badge variant="warning">Edited by you</Badge>}
        {page.synthesized_at && <span>Written by Janus {formatDate(page.synthesized_at)}</span>}
      </div>
    </div>
    {!editing && (
      <div className="flex gap-2">
        <Button type="button" variant="ghost" icon={<Pencil size={16} />} onClick={onEdit}>
          Edit
        </Button>
        <Button
          type="button"
          variant="ghost"
          icon={<History size={16} />}
          onClick={onToggleHistory}
        >
          History
        </Button>
      </div>
    )}
  </div>
);

/**
 * One wiki page (JAN-244): text with sources, editing, proposals to review, and history. The Sources tab
 * (JAN-334) resolves each `[n]` to its raw passage or memory fact; clicking a marker in the text opens it there.
 */
export const WikiPageView: React.FC<{ pageId: number; onOpenPage?: (pageId: number) => void }> = ({
  pageId,
  onOpenPage,
}) => {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [tab, setTab] = useState<{ id: WikiPageTab; focus: number | null }>({ id: 'page', focus: null });
  const { data: page, isLoading, isError } = useQuery<WikiPageDetail>({
    queryKey: ['wiki-page', pageId],
    queryFn: async () => (await wikiApi.getPage(pageId)).data,
  });
  const refresh = (response?: { data: WikiPageDetail }) => {
    if (response) queryClient.setQueryData(['wiki-page', pageId], response.data);
    queryClient.invalidateQueries({ queryKey: ['wiki-pages'] });
    queryClient.invalidateQueries({ queryKey: ['wiki-provenance', pageId] });
  };
  const save = useMutation({
    mutationFn: (body: string) => wikiApi.editPage(pageId, body),
    onSuccess: (response) => {
      refresh(response);
      setDraft(null);
    },
  });
  const accept = useMutation({ mutationFn: (id: number) => wikiApi.acceptRevision(id), onSuccess: refresh });
  const restore = useMutation({ mutationFn: (id: number) => wikiApi.restoreRevision(id), onSuccess: refresh });
  const dismiss = useMutation({
    mutationFn: (id: number) => wikiApi.dismissRevision(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['wiki-page', pageId] });
      queryClient.invalidateQueries({ queryKey: ['wiki-pages'] });
    },
  });

  if (isLoading) return <Card className="p-6 text-sm text-gray-500">Loading page…</Card>;
  if (isError || !page) {
    return (
      <Card className="p-6">
        <p className="text-sm text-red-600 dark:text-red-400" role="alert">
          Could not load this page.
        </p>
      </Card>
    );
  }
  const showSources = tab.id === 'sources' && draft === null;

  return (
    <Card className="p-6 space-y-4" aria-label="Wiki page">
      <PageHeader
        page={page}
        editing={draft !== null}
        onEdit={() => setDraft(page.body)}
        onToggleHistory={() => setShowHistory((value) => !value)}
      />

      {draft === null && (
        <WikiPageTabs pageId={page.id} current={tab.id} onChange={(id) => setTab({ id, focus: null })} />
      )}
      {showSources ? (
        <div role="tabpanel" id={`wiki-tabpanel-${page.id}-sources`} aria-labelledby={`wiki-tab-${page.id}-sources`}>
          <WikiProvenance key={tab.focus ?? 'all'} pageId={page.id} focus={tab.focus} onOpenPage={onOpenPage} />
        </div>
      ) : (
        <div role="tabpanel" id={`wiki-tabpanel-${page.id}-page`} aria-labelledby={`wiki-tab-${page.id}-page`} className="space-y-4">
          {page.proposals.map((proposal) => (
            <ProposalCard
              key={proposal.id}
              page={page}
              proposal={proposal}
              accepting={accept.isPending}
              dismissing={dismiss.isPending}
              onAccept={() => accept.mutate(proposal.id)}
              onDismiss={() => dismiss.mutate(proposal.id)}
            />
          ))}
          {draft !== null ? (
            <EditForm pageId={page.id} draft={draft} onDraft={setDraft} save={save} />
          ) : page.body ? (
            <>
              <PageText body={page.body} citations={page.citations} onCite={(n) => setTab({ id: 'sources', focus: n })} />
              <Sources citations={page.citations} />
            </>
          ) : (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Not written yet. Wiki synthesis writes this page on its next run.
            </p>
          )}
          {showHistory && draft === null && (
            <HistoryList
              revisions={page.revisions}
              restoring={restore.isPending ? (restore.variables ?? null) : null}
              onRestore={(id) => restore.mutate(id)}
            />
          )}
        </div>
      )}
    </Card>
  );
};
