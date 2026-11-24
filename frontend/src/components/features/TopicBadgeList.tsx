import React from 'react';
import { Eye, Pin } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type { EmailTopicBadge } from '../../types';
import { buildTopicDetailPath } from '../../lib/topicNavigation';
import { Badge } from '../ui/Badge';

interface TopicBadgeListProps {
  topics: EmailTopicBadge[];
  stopPropagation?: boolean;
  className?: string;
}

const badgeVariantByStatus: Record<EmailTopicBadge['status'], 'secondary' | 'success' | 'warning' | 'gray'> = {
  candidate: 'secondary',
  active: 'success',
  snoozed: 'warning',
  resolved: 'gray',
  ignored: 'gray',
};

export const TopicBadgeList: React.FC<TopicBadgeListProps> = ({ topics, stopPropagation = false, className }) => {
  const navigate = useNavigate();

  if (topics.length === 0) {
    return null;
  }

  return (
    <div className={className ?? 'flex flex-wrap gap-2'}>
      {topics.map((topic) => {
        const isWatching = topic.status === 'candidate';
        return (
          <button
            key={topic.id}
            type="button"
            onClick={(event) => {
              if (stopPropagation) {
                event.stopPropagation();
              }
              navigate(buildTopicDetailPath(topic.id));
            }}
            className="rounded-full focus:outline-none focus:ring-2 focus:ring-primary/30"
            aria-label={isWatching ? `Open topic ${topic.title} (watching)` : `Open topic ${topic.title}`}
            title={isWatching ? 'Janus is watching this topic until more mail confirms it' : undefined}
          >
            <Badge
              variant={topic.pinned ? 'primary' : badgeVariantByStatus[topic.status]}
              className={
                isWatching
                  ? 'inline-flex items-center gap-1 normal-case tracking-normal border border-dashed border-current/40'
                  : 'inline-flex items-center gap-1 normal-case tracking-normal'
              }
            >
              {topic.pinned && <Pin size={10} />}
              {isWatching && <Eye size={10} />}
              {topic.title}
            </Badge>
          </button>
        );
      })}
    </div>
  );
};
