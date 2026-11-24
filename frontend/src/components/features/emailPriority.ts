import type { LucideIcon } from 'lucide-react';
import { Archive, MinusCircle, ShieldAlert, Zap } from 'lucide-react';
import type { Email } from '../../types';

type ButtonVariant = 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger';

export const getPriorityBadgeVariant = (priority: Email['priority']): 'primary' | 'gray' | 'danger' => {
  switch (priority) {
    case 'important':
      return 'primary';
    case 'spam':
      return 'danger';
    default:
      return 'gray';
  }
};

export const EMAIL_PRIORITY_ACTIONS: Array<{
  priority: Email['priority'];
  label: string;
  icon: LucideIcon;
  variant: ButtonVariant;
}> = [
  { priority: 'important', label: 'Important', icon: Zap, variant: 'primary' },
  { priority: 'digest', label: 'Digest', icon: Archive, variant: 'secondary' },
  { priority: 'ignore', label: 'Ignore', icon: MinusCircle, variant: 'secondary' },
  { priority: 'spam', label: 'Spam', icon: ShieldAlert, variant: 'danger' },
];
