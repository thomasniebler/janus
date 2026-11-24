import { formatServerDate } from '../../lib/serverTime';
import React, { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BookOpen, Brain, FileText } from 'lucide-react';
import { knowledgeStackApi, type ProvenanceCitation, type ProvenanceFact, type WikiPageProvenance } from '../../lib/knowledgeStack';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';

const formatDate = (value?: string | null) => formatServerDate(value) ?? '';

const SOURCE_LABELS: Record<string, string> = {
  email: 'Email',
  email_attachment: 'Attachment',
  conversation: 'Chat',
  whatsapp_conversation: 'WhatsApp',
  calendar_event: 'Calendar',
  planner_task: 'Task',
  upload: 'Document',
  memory_fact: 'Memory fact',
};

const FactText: React.FC<{ fact: ProvenanceFact }> = ({ fact }) => (
  <div className="space-y-1">
    <p className="text-sm font-medium">{fact.summary}</p>
    {fact.detail && <p className="text-sm text-gray-600 dark:text-gray-300 whitespace-pre-wrap">{fact.detail}</p>}
    <p className="text-xs text-gray-500 dark:text-gray-400">
      {[fact.status, fact.source && `from ${fact.source}`, fact.updated_at && `updated ${formatDate(fact.updated_at)}`]
        .filter(Boolean)
        .join(' · ')}
    </p>
  </div>
);

/** The raw passage or memory fact behind one marker, or why it cannot be shown. */
const CitationDetail: React.FC<{ citation: ProvenanceCitation; entityNames: Map<number, string> }> = ({
  citation,
  entityNames,
}) => {
  if (!citation.available) {
    return (
      <p className="text-sm text-gray-500 dark:text-gray-400">
        This {citation.kind === 'fact' ? 'memory fact' : 'passage'} no longer exists; the page still shows what it was
        written from.
      </p>
    );
  }
  if (citation.kind === 'fact' && citation.fact) return <FactText fact={citation.fact} />;
  const mentioned = citation.entity_ids.map((id) => entityNames.get(id)).filter(Boolean);
  return (
    <div className="space-y-2">
      <blockquote
        className="text-sm whitespace-pre-wrap border-l-2 border-gray-200 dark:border-gray-700 pl-3 text-gray-700 dark:text-gray-200"
        aria-label={`Passage ${citation.n}`}
      >
        {citation.excerpt}
      </blockquote>
      {mentioned.length > 0 && (
        <p className="text-xs text-gray-500 dark:text-gray-400">Mentions: {mentioned.join(', ')}</p>
      )}
    </div>
  );
};

type CitationItemProps = {
  citation: ProvenanceCitation;
  open: boolean;
  focused: boolean;
  onToggle: () => void;
  entityNames: Map<number, string>;
};

const CitationItem: React.FC<CitationItemProps> = ({ citation, open, focused, onToggle, entityNames }) => {
  const ref = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (focused) ref.current?.scrollIntoView?.({ block: 'nearest' });
  }, [focused]);
  const Icon = citation.kind === 'fact' ? Brain : FileText;
  return (
    <li
      ref={ref}
      id={`wiki-provenance-${citation.n}`}
      aria-current={focused ? 'true' : undefined}
      className={`rounded-xl border p-3 space-y-2 ${
        focused ? 'border-primary' : 'border-gray-200 dark:border-gray-700'
      }`}
    >
      <button
        type="button"
        className="w-full flex items-start gap-2 text-left text-sm"
        aria-expanded={open}
        onClick={onToggle}
      >
        <Icon size={16} className="mt-0.5 shrink-0 text-gray-400" aria-hidden />
        <span className="flex-1">
          <span className="font-semibold">[{citation.n}]</span> {citation.title || '(untitled)'}
          <span className="block text-xs text-gray-500 dark:text-gray-400">
            {[SOURCE_LABELS[citation.source_type ?? ''] ?? citation.source_type, formatDate(citation.date)]
              .filter(Boolean)
              .join(' · ')}
          </span>
        </span>
        {!citation.available && <Badge variant="gray">gone</Badge>}
      </button>
      {open && <CitationDetail citation={citation} entityNames={entityNames} />}
    </li>
  );
};

type Props = {
  pageId: number;
  /** The `[n]` marker the user followed from the page text; it starts expanded and highlighted. */
  focus?: number | null;
  /** Opens another wiki page (an entity's page) in the surrounding view. */
  onOpenPage?: (pageId: number) => void;
};

const Section: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <section className="space-y-2" aria-label={title}>
    <h4 className="text-xs font-bold text-gray-400 uppercase">{title}</h4>
    {children}
  </section>
);

const Entities: React.FC<{ data: WikiPageProvenance; onOpenPage?: (pageId: number) => void }> = ({ data, onOpenPage }) =>
  data.entities.length === 0 ? (
    <p className="text-sm text-gray-500 dark:text-gray-400">No entities extracted from these passages yet.</p>
  ) : (
    <ul className="flex flex-wrap gap-2" aria-label="Linked entities">
      {data.entities.map((entity) => (
        <li key={entity.id} className="flex items-center gap-1 rounded-xl border border-gray-200 dark:border-gray-700 px-2 py-1 text-sm">
          <span className="font-medium">{entity.name}</span>
          <Badge variant="gray">{entity.is_subject ? 'this page' : entity.type}</Badge>
          {entity.citations.length > 0 && (
            <span className="text-xs text-gray-500 dark:text-gray-400">{entity.citations.map((n) => `[${n}]`).join('')}</span>
          )}
          {entity.wiki_page_id && onOpenPage && (
            <Button
              type="button"
              variant="ghost"
              icon={<BookOpen size={14} />}
              aria-label={`Open the wiki page about ${entity.name}`}
              onClick={() => onOpenPage(entity.wiki_page_id!)}
            />
          )}
        </li>
      ))}
    </ul>
  );

const Facts: React.FC<{ facts: ProvenanceFact[] }> = ({ facts }) =>
  facts.length === 0 ? (
    <p className="text-sm text-gray-500 dark:text-gray-400">No memory facts about this subject.</p>
  ) : (
    <ul className="space-y-2" aria-label="Linked facts">
      {facts.map((fact) => (
        <li key={fact.id} className="rounded-xl border border-gray-200 dark:border-gray-700 p-3 flex gap-2">
          <Brain size={16} className="mt-0.5 shrink-0 text-gray-400" aria-hidden />
          <div className="flex-1">
            <FactText fact={fact} />
          </div>
          <span className="text-xs text-gray-500 dark:text-gray-400 shrink-0">
            {fact.citations.length > 0 ? `cited ${fact.citations.map((n) => `[${n}]`).join('')}` : 'not cited yet'}
          </span>
        </li>
      ))}
    </ul>
  );

/**
 * The Sources tab of a wiki page (JAN-334): every `[n]` marker resolved to the raw passage or memory fact it
 * cites, the entities mentioned in those passages, and the memory facts about the page's subject.
 */
export const WikiProvenance: React.FC<Props> = ({ pageId, focus = null, onOpenPage }) => {
  const [opened, setOpened] = useState<Set<number>>(() => new Set(focus !== null ? [focus] : []));
  const { data, isLoading, isError } = useQuery<WikiPageProvenance>({
    queryKey: ['wiki-provenance', pageId],
    queryFn: async () => (await knowledgeStackApi.getProvenance(pageId)).data,
  });
  if (isLoading) return <p className="text-sm text-gray-500 dark:text-gray-400">Loading sources…</p>;
  if (isError || !data) {
    return (
      <p className="text-sm text-red-600 dark:text-red-400" role="alert">
        Could not load the sources of this page.
      </p>
    );
  }
  const entityNames = new Map(data.entities.map((entity) => [entity.id, entity.name]));
  const toggle = (n: number) =>
    setOpened((current) => {
      const next = new Set(current);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });
  return (
    <div className="space-y-6">
      <p className="text-sm text-gray-500 dark:text-gray-400">
        About the {data.subject.type} “{data.subject.title}”{data.subject.available ? '' : ' (no longer tracked)'}.
      </p>
      <Section title="Citations">
        {data.citations.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-gray-400">This page cites nothing yet.</p>
        ) : (
          <ol className="space-y-2" aria-label="Citations">
            {data.citations.map((citation) => (
              <CitationItem
                key={citation.n}
                citation={citation}
                open={opened.has(citation.n)}
                focused={focus === citation.n}
                onToggle={() => toggle(citation.n)}
                entityNames={entityNames}
              />
            ))}
          </ol>
        )}
      </Section>
      <Section title="Entities">
        <Entities data={data} onOpenPage={onOpenPage} />
      </Section>
      <Section title="Memory facts">
        <Facts facts={data.facts} />
      </Section>
    </div>
  );
};
