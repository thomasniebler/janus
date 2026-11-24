import { formatServerDate } from '../../lib/serverTime';
import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { knowledgeApi } from '../../api';
import type { KnowledgeSearchResult } from '../../types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';
import { Input } from '../ui/Input';

const SOURCE_LABELS: Record<string, string> = {
  email: 'Email',
  email_attachment: 'Attachment',
  conversation: 'Chat',
  whatsapp_conversation: 'WhatsApp',
  calendar_event: 'Calendar',
  planner_task: 'Task',
  upload: 'Upload',
};

const formatDate = (value?: string | null) => formatServerDate(value) ?? '';

interface KnowledgeSearchPanelProps {
  /** Opens an entity's detail when one of a hit's entities is clicked. */
  onSelectEntity: (entityId: number) => void;
}

/** Search across everything ingested (JAN-243): meaning, exact words, and the knowledge graph, with sources. */
export const KnowledgeSearchPanel: React.FC<KnowledgeSearchPanelProps> = ({ onSelectEntity }) => {
  const [draft, setDraft] = useState('');
  const [query, setQuery] = useState('');
  const { data, isFetching, isError } = useQuery<KnowledgeSearchResult>({
    queryKey: ['knowledge-search', query],
    queryFn: async () => (await knowledgeApi.search({ query, hops: 1 })).data,
    enabled: query.length > 0,
  });

  return (
    <Card className="p-6 space-y-4">
      <h3 className="text-lg font-bold">Search your knowledge</h3>
      <form
        className="flex items-end gap-2"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(draft.trim());
        }}
      >
        <Input
          label="Search mail, chats, calendar, tasks, and documents"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="e.g. Apollo budget"
        />
        <Button type="submit" icon={<Search size={16} />} isLoading={isFetching} disabled={!draft.trim()}>
          Search
        </Button>
      </form>

      {isError && (
        <p className="text-sm text-red-600 dark:text-red-400" role="alert">
          Search failed.
        </p>
      )}
      {data && data.entities.length > 0 && (
        <p className="text-xs text-gray-500 dark:text-gray-400">
          Also following:{' '}
          {data.entities.map((entity, index) => (
            <React.Fragment key={entity.id}>
              {index > 0 && ', '}
              <button type="button" className="underline" onClick={() => onSelectEntity(entity.id)}>
                {entity.name}
              </button>
            </React.Fragment>
          ))}
        </p>
      )}
      {data && data.hits.length === 0 && (
        <p className="text-sm text-gray-500 dark:text-gray-400">Nothing found for “{query}”.</p>
      )}
      {data && data.hits.length > 0 && (
        <ol className="space-y-4" aria-label="Search results">
          {data.hits.map((hit) => (
            <li key={hit.chunk_id} className="space-y-1">
              <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                <Badge variant="gray">{SOURCE_LABELS[hit.source.type] ?? hit.source.type}</Badge>
                <span className="font-medium text-gray-700 dark:text-gray-200">{hit.source.title || '(untitled)'}</span>
                {hit.source.date && <span>{formatDate(hit.source.date)}</span>}
                {hit.matched_by.map((reason) => (
                  <Badge key={reason} variant={reason === 'query' ? 'success' : 'warning'}>
                    {reason === 'query' ? 'matches your search' : reason}
                  </Badge>
                ))}
              </div>
              <p className="text-sm whitespace-pre-line line-clamp-4">{hit.text}</p>
              {hit.entities.length > 0 && (
                <div className="flex flex-wrap gap-2 text-xs">
                  {hit.entities.map((entity) => (
                    <button
                      key={entity.id}
                      type="button"
                      className="underline text-gray-600 dark:text-gray-300"
                      onClick={() => onSelectEntity(entity.id)}
                    >
                      {entity.name}
                    </button>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
};
