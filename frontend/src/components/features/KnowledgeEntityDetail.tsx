import { formatServerDate } from '../../lib/serverTime';
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, ArrowRight, X } from 'lucide-react';
import { knowledgeApi } from '../../api';
import type { KnowledgeEntityDetail as Detail } from '../../types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';

const humanize = (predicate: string) => predicate.replace(/_/g, ' ');

interface KnowledgeEntityDetailProps {
  entityId: number;
  onSelectEntity: (entityId: number) => void;
  onClose: () => void;
}

/** One entity (JAN-243): aliases, description, relations in both directions, and the sources mentioning it. */
export const KnowledgeEntityDetail: React.FC<KnowledgeEntityDetailProps> = ({ entityId, onSelectEntity, onClose }) => {
  const { data, isLoading, isError } = useQuery<Detail>({
    queryKey: ['knowledge-entity', entityId],
    queryFn: async () => (await knowledgeApi.getEntity(entityId)).data,
  });

  return (
    <Card className="p-6 space-y-4" aria-label="Entity detail">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <h3 className="text-lg font-bold">{data?.name ?? 'Entity'}</h3>
          {data && (
            <p className="text-xs text-gray-500 dark:text-gray-400">
              {data.type}
              {data.aliases.length > 0 ? ` · also ${data.aliases.join(', ')}` : ''}
            </p>
          )}
        </div>
        <Button type="button" variant="ghost" aria-label="Close entity" icon={<X size={16} />} onClick={onClose} />
      </div>
      {isLoading && <p className="text-sm text-gray-500 dark:text-gray-400">Loading…</p>}
      {isError && (
        <p className="text-sm text-red-600 dark:text-red-400" role="alert">
          Could not load this entity.
        </p>
      )}
      {data && (
        <>
          {data.description && <p className="text-sm">{data.description}</p>}
          <div className="space-y-2">
            <h4 className="text-sm font-bold">Relations</h4>
            {data.relations.length === 0 ? (
              <p className="text-sm text-gray-500 dark:text-gray-400">No relations recorded yet.</p>
            ) : (
              <ul className="space-y-1 text-sm" aria-label="Relations">
                {data.relations.map((relation) => (
                  <li key={relation.id} className="flex flex-wrap items-center gap-2">
                    {relation.direction === 'outgoing' ? <ArrowRight size={14} /> : <ArrowLeft size={14} />}
                    <span>
                      {relation.direction === 'outgoing'
                        ? `${data.name} ${humanize(relation.predicate)} `
                        : `${humanize(relation.predicate)} by `}
                    </span>
                    <button type="button" className="underline" onClick={() => onSelectEntity(relation.other.id)}>
                      {relation.other.name}
                    </button>
                    <Badge variant={relation.confidence === 'high' ? 'success' : 'gray'}>{relation.confidence}</Badge>
                    <span className="text-xs text-gray-500 dark:text-gray-400">
                      {relation.supporting_chunks} passage
                      {relation.supporting_chunks === 1 ? '' : 's'}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="space-y-2">
            <h4 className="text-sm font-bold">Mentioned in</h4>
            <ul className="space-y-1 text-sm" aria-label="Mentioned in">
              {data.sources.map((source) => (
                <li key={source.id}>
                  {source.title || '(untitled)'}{' '}
                  <span className="text-xs text-gray-500 dark:text-gray-400">
                    {source.type}
                    {source.date ? ` · ${formatServerDate(source.date) ?? source.date}` : ''} · {source.mentions} passage
                    {source.mentions === 1 ? '' : 's'}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </Card>
  );
};
