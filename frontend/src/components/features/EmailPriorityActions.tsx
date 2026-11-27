import React from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { emailApi } from '../../api';
import type { Email } from '../../types';
import { Button } from '../ui/Button';
import { EMAIL_PRIORITY_ACTIONS } from './emailPriority';

interface EmailPriorityActionsProps {
  email: Email;
  size?: 'sm' | 'md';
  label?: string;
  stopPropagation?: boolean;
}

export const EmailPriorityActions: React.FC<EmailPriorityActionsProps> = ({
  email,
  size = 'sm',
  label,
  stopPropagation = false,
}) => {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: ({ priority }: { priority: Email['priority'] }) => emailApi.updatePriority(email.id, priority),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['emails-list'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
    },
  });

  const handleClick =
    (priority: Email['priority']) =>
    (event: React.MouseEvent<HTMLButtonElement>) => {
      if (stopPropagation) {
        event.stopPropagation();
      }
      mutation.mutate({ priority });
    };

  return (
    <div className="space-y-2">
      {label && <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">{label}</p>}
      <div className="flex flex-wrap gap-2">
        {EMAIL_PRIORITY_ACTIONS.map((action) => (
          <Button
            key={action.priority}
            size={size}
            variant={email.priority === action.priority ? action.variant : 'outline'}
            icon={<action.icon size={14} />}
            isLoading={mutation.isPending && mutation.variables?.priority === action.priority}
            onClick={handleClick(action.priority)}
          >
            {action.label}
          </Button>
        ))}
      </div>
    </div>
  );
};
