import { formatServerDateTime } from '../../lib/serverTime';
import React from 'react';
import { Clock, MailOpen, MessageSquare, X } from 'lucide-react';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';
import type { Email } from '../../types';
import { EmailPriorityActions } from './EmailPriorityActions';
import { getPriorityBadgeVariant } from './emailPriority';
import { TopicBadgeList } from './TopicBadgeList';

interface EmailDetailViewProps {
  email: Email;
  onClose: () => void;
}

export const EmailDetailView: React.FC<EmailDetailViewProps> = ({ email, onClose }) => {
  return (
    <Card className="p-6 space-y-4 border-primary/20">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2 mb-2">
            <Badge variant={getPriorityBadgeVariant(email.priority)}>{email.priority}</Badge>
            {email.classification_confidence && (
              <Badge variant="gray">{email.classification_confidence} confidence</Badge>
            )}
          </div>
          <h2 className="text-xl font-bold tracking-tight">{email.subject}</h2>
        </div>
        <Button size="sm" variant="outline" onClick={onClose} aria-label="Close email" title="Close email">
          <X size={14} />
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm text-gray-600 dark:text-gray-300">
        <div className="flex items-center gap-2">
          <MailOpen size={14} className="text-primary" />
          <span className="font-medium">From:</span>
          <span>{email.sender}</span>
        </div>
        <div className="flex items-center gap-2">
          <Clock size={14} className="text-primary" />
          <span className="font-medium">Received:</span>
          <span>{formatServerDateTime(email.received_at) ?? email.received_at}</span>
        </div>
      </div>

      <div className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-primary">Tracked topics</h3>
        {email.topics.length > 0 ? (
          <TopicBadgeList topics={email.topics} className="flex flex-wrap gap-2" />
        ) : (
          <p className="text-sm text-gray-500 dark:text-gray-400">No tracked topics are linked to this email yet.</p>
        )}
      </div>

      <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-4 bg-gray-50 dark:bg-gray-800/50 space-y-3">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-primary">Email Actions</h3>
          <p className="text-sm text-gray-600 dark:text-gray-300 mt-1">
            Update how Janus should prioritize this email.
          </p>
        </div>

        <EmailPriorityActions email={email} />
      </div>

      {email.reasoning && (
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 bg-gray-50 dark:bg-gray-800/50">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-primary mb-1 flex items-center gap-1">
            <MessageSquare size={12} />
            AI Reasoning
          </h3>
          <p className="text-sm text-gray-600 dark:text-gray-300">{email.reasoning}</p>
        </div>
      )}

      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-primary mb-2">Message Content</h3>
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-4 bg-white dark:bg-dark-bg/50">
          <p className="text-sm leading-relaxed whitespace-pre-wrap text-gray-700 dark:text-gray-200">
            {email.snippet}
          </p>
        </div>
      </div>
    </Card>
  );
};
