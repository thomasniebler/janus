import { formatServerDateTime } from '../../lib/serverTime';
import React, { useMemo } from 'react';
import { ArrowRight, Pin, Tags } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { buildTopicDetailPath } from '../../lib/topicNavigation';
import type { TopicSummary } from '../../types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';

interface TopicFocusWidgetProps {
  topics: TopicSummary[];
}

const formatLastSeen = (timestamp: string) =>
  formatServerDateTime(timestamp, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });

const statusBadgeVariant = (status: TopicSummary['status']) => {
  if (status === 'active') {
    return 'success';
  }
  if (status === 'snoozed') {
    return 'warning';
  }
  return 'gray';
};

export const TopicFocusWidget: React.FC<TopicFocusWidgetProps> = ({ topics }) => {
  const navigate = useNavigate();

  const { sortedTopics, pinnedCount } = useMemo(() => {
    const sortedTopics = [...topics].sort((left, right) => {
        if (left.pinned !== right.pinned) {
          return Number(right.pinned) - Number(left.pinned);
        }
        return right.last_seen_at.localeCompare(left.last_seen_at);
      });

    return {
      sortedTopics,
      pinnedCount: sortedTopics.reduce((count, topic) => count + Number(topic.pinned), 0),
    };
  }, [topics]);
  const openTopicManager = () => navigate('/memory?tab=topics');
  const openTopicDetail = (topicId: number) => navigate(buildTopicDetailPath(topicId));

  if (sortedTopics.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-gray-50 text-gray-300 dark:bg-gray-800 dark:text-gray-600">
          <Tags size={28} />
        </div>
        <div className="space-y-2">
          <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">No active topics yet</p>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            As Janus spots recurring work across your inbox, the threads that matter most will show up here.
          </p>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={openTopicManager} icon={<ArrowRight size={14} />}>
          Open topics
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="primary">{sortedTopics.length} Active</Badge>
        <Badge variant="gray">{pinnedCount} Pinned</Badge>
      </div>

      <div className="space-y-3">
        {sortedTopics.map((topic) => (
          <button
            key={topic.id}
            data-ui-item={`topic:${topic.id}`}
            type="button"
            onClick={() => openTopicDetail(topic.id)}
            className="w-full rounded-2xl border border-gray-100 p-4 text-left transition-colors hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-800/60"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">{topic.title}</p>
                  {topic.pinned && <Badge variant="primary">Pinned</Badge>}
                  <Badge variant={statusBadgeVariant(topic.status)}>{topic.status}</Badge>
                  {topic.confidence && <Badge variant="secondary">{topic.confidence}</Badge>}
                </div>
                {topic.summary && (
                  <p className="mt-2 line-clamp-2 text-sm text-gray-600 dark:text-gray-300">{topic.summary}</p>
                )}
                <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
                  {topic.evidence_count} evidence item{topic.evidence_count === 1 ? '' : 's'} • Last seen{' '}
                  {formatLastSeen(topic.last_seen_at)}
                </p>
              </div>
              <ArrowRight size={16} className="mt-1 shrink-0 text-gray-300 dark:text-gray-600" />
            </div>
          </button>
        ))}
      </div>

      <div className="flex justify-end">
        <Button type="button" variant="outline" size="sm" onClick={openTopicManager} icon={<Pin size={14} />}>
          Manage topics
        </Button>
      </div>
    </div>
  );
};
