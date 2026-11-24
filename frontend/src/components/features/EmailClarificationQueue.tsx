import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, HelpCircle, RefreshCw, X } from 'lucide-react';
import { emailApi } from '../../api';
import type { EmailClarificationRequest } from '../../types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';

const confidenceBadge = (confidence: EmailClarificationRequest['suggestion_confidence']) => {
  switch (confidence) {
    case 'high':
      return <Badge variant="primary">high confidence</Badge>;
    case 'medium':
      return <Badge variant="gray">medium confidence</Badge>;
    default:
      return <Badge variant="danger">low confidence</Badge>;
  }
};

export const EmailClarificationQueue: React.FC = () => {
  const queryClient = useQueryClient();
  const { data: clarifications, isLoading } = useQuery({
    queryKey: ['email-clarifications'],
    queryFn: async () => (await emailApi.listClarifications()).data,
  });

  const refreshQueries = () => {
    queryClient.invalidateQueries({ queryKey: ['email-clarifications'] });
    queryClient.invalidateQueries({ queryKey: ['email-rules'] });
    queryClient.invalidateQueries({ queryKey: ['emails-list'] });
    queryClient.invalidateQueries({ queryKey: ['dashboard'] });
  };

  const approveMutation = useMutation({
    mutationFn: (id: number) => emailApi.approveClarification(id),
    onSuccess: refreshQueries,
  });

  const dismissMutation = useMutation({
    mutationFn: (id: number) => emailApi.dismissClarification(id),
    onSuccess: refreshQueries,
  });

  const pendingClarifications = (clarifications ?? []).filter((item) => item.status === 'pending');

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <HelpCircle size={18} className="text-primary" />
        <h3 className="text-lg font-bold">Clarify misunderstandings</h3>
      </div>

      {isLoading ? (
        <Card className="h-28 flex items-center justify-center">
          <RefreshCw className="animate-spin text-primary" />
        </Card>
      ) : pendingClarifications.length === 0 ? (
        <Card className="p-6">
          <p className="text-sm text-gray-500 dark:text-gray-400">
            No pending clarifications. When you correct Janus, suggested future rules will show up here for review.
          </p>
        </Card>
      ) : (
        pendingClarifications.map((item) => (
          <Card key={item.id} className="p-4 space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="gray">{item.previous_priority}</Badge>
              <span className="text-sm text-gray-400">→</span>
              <Badge variant="primary">{item.requested_priority}</Badge>
              {confidenceBadge(item.suggestion_confidence)}
            </div>

            <div className="space-y-1">
              <h4 className="font-semibold text-sm">{item.email.subject}</h4>
              <p className="text-xs text-gray-500 dark:text-gray-400">{item.email.sender}</p>
              <p className="text-sm text-gray-600 dark:text-gray-300">{item.suggestion_reason}</p>
            </div>

            <div className="rounded-xl bg-gray-50 dark:bg-gray-800/60 p-3 text-sm space-y-1">
              <p><span className="font-semibold">Suggested rule:</span> {item.suggested_rule_name}</p>
              <p><span className="font-semibold">Match:</span> {item.suggested_rule_field} =~ <code>{item.suggested_rule_pattern}</code></p>
              <p><span className="font-semibold">Apply label:</span> {item.suggested_rule_priority}</p>
            </div>

            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={() => approveMutation.mutate(item.id)}
                isLoading={approveMutation.isPending && approveMutation.variables === item.id}
                icon={<Check size={14} />}
              >
                Approve rule
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => dismissMutation.mutate(item.id)}
                isLoading={dismissMutation.isPending && dismissMutation.variables === item.id}
                icon={<X size={14} />}
              >
                Dismiss
              </Button>
            </div>
          </Card>
        ))
      )}
    </div>
  );
};
