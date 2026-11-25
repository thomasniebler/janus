import React from 'react';
import { Lightbulb } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { skillSuggestionsApi, type SkillSuggestion } from '../../skillSuggestionsApi';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';

/**
 * Routines Janus noticed in your chats (the same tools in the same order, three times or more), offered as
 * skills to write (JAN-407). "Draft skill" only prefills the skill designer; nothing is saved until you save it.
 */
export const SkillSuggestions: React.FC<{ onDraft: (brief: string) => void }> = ({ onDraft }) => {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ['skill-suggestions'], queryFn: async () => (await skillSuggestionsApi.list()).data });
  const dismiss = useMutation({
    mutationFn: (key: string) => skillSuggestionsApi.dismiss(key),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['skill-suggestions'] }),
  });
  if (!data || data.length === 0) return null;
  return (
    <Card className="space-y-3 p-4" data-testid="skill-suggestions">
      <div className="flex items-center gap-2">
        <Lightbulb size={16} className="text-primary" />
        <h3 className="text-sm font-semibold">Suggested skills</h3>
      </div>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Janus used the same tools in the same order in several recent chats. A skill can make that routine reliable.
      </p>
      <ul className="space-y-2">
        {data.map((suggestion: SkillSuggestion) => (
          <li key={suggestion.key} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-white/10 p-3">
            <div className="min-w-0">
              <p className="font-mono text-sm">{suggestion.tools.join(' → ')}</p>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                In {suggestion.turns} chats{suggestion.examples.length > 0 ? `, e.g. ${suggestion.examples.join(', ')}` : ''}
              </p>
            </div>
            <div className="flex gap-2">
              <Button size="sm" onClick={() => onDraft(suggestion.brief)}>Draft skill</Button>
              <Button size="sm" variant="ghost" isLoading={dismiss.isPending && dismiss.variables === suggestion.key} onClick={() => dismiss.mutate(suggestion.key)}>
                Not now
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
};
