import { formatServerDateTime } from '../../lib/serverTime';
import React from 'react';
import { Zap, Archive, Clock, ShieldAlert, MinusCircle } from 'lucide-react';
import { Badge } from '../ui/Badge';
import type { Email } from '../../types';
import { EmailPriorityActions } from './EmailPriorityActions';
import { getPriorityBadgeVariant } from './emailPriority';
import { TopicBadgeList } from './TopicBadgeList';

interface EmailItemProps {
  email: Email;
  onOpenEmail?: (emailId: number) => void;
}

export const EmailItem: React.FC<EmailItemProps> = ({ email, onOpenEmail }) => {
  const getConfidenceBadge = (confidence?: string | null) => {
    switch (confidence) {
      case 'high':
        return <Badge variant="primary">high confidence</Badge>;
      case 'medium':
        return <Badge variant="gray">medium confidence</Badge>;
      case 'low':
        return <Badge variant="danger">low confidence</Badge>;
      default:
        return null;
    }
  };

  const getPriorityIcon = (priority: string) => {
    switch (priority) {
      case 'important': return <Zap size={20} />;
      case 'digest': return <Archive size={20} />;
      case 'ignore': return <MinusCircle size={20} />;
      case 'spam': return <ShieldAlert size={20} />;
      default: return <Archive size={20} />;
    }
  };

  return (
    <div
      data-ui-item={`email:${email.id}`}
      className="transition-all duration-200 hover:bg-gray-50 dark:hover:bg-gray-800/50"
    >
      <div
        className="p-5 cursor-pointer flex items-start gap-4 group"
        onClick={() => onOpenEmail?.(email.id)}
      >
        <div className={`mt-1 p-2 rounded-lg ${
          email.priority === 'important' ? 'bg-primary/10 text-primary' :
          email.priority === 'ignore' ? 'bg-gray-100 dark:bg-gray-800 text-gray-500' :
          email.priority === 'spam' ? 'bg-red-100 text-red-600' : 'bg-gray-100 dark:bg-gray-800 text-gray-400'
        }`}>
          {getPriorityIcon(email.priority)}
        </div>

        <div className="flex-grow min-w-0">
          <div className="flex justify-between items-start mb-1">
            <div className="flex items-center gap-2 min-w-0">
              <span className="font-bold text-sm truncate">{email.sender}</span>
              <Badge variant={getPriorityBadgeVariant(email.priority)}>{email.priority}</Badge>
              {getConfidenceBadge(email.classification_confidence)}
            </div>
            <div className="flex items-center gap-2 text-gray-400 text-xs whitespace-nowrap">
              <Clock size={12} />
              {formatServerDateTime(email.received_at, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) ?? email.received_at}
            </div>
          </div>
          <h3 className="font-semibold text-base mb-1 text-light-text dark:text-dark-text transition-colors group-hover:text-primary">
            {email.subject}
          </h3>
          <p className="text-sm text-gray-500 dark:text-gray-400 italic line-clamp-1">
            "{email.snippet}"
          </p>
          <TopicBadgeList topics={email.topics} stopPropagation className="mt-3 flex flex-wrap gap-2" />

          <div className="mt-3 border-t border-gray-100 pt-3 dark:border-gray-800">
            <EmailPriorityActions email={email} label="Classify from list" stopPropagation />
          </div>
        </div>
      </div>
    </div>
  );
};
