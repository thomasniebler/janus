import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { triggerDefinitionApi } from '../../../api';
import type { TriggerDefinition } from '../../../types';

export const triggerDefinitionQueryKey = ['trigger-definitions'] as const;

export const browserTimezone = (): string => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
};

/** The user's trigger definitions; shared by the definitions card and the run filters so both use one cache entry. */
export const useTriggerDefinitions = () =>
  useQuery({
    queryKey: triggerDefinitionQueryKey,
    queryFn: async () => (await triggerDefinitionApi.list(browserTimezone())).data,
    refetchInterval: 15000,
  });

/** Re-reads the definitions and the run history after a change. */
export const useRefreshTriggerDefinitions = () => {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: triggerDefinitionQueryKey });
    queryClient.invalidateQueries({ queryKey: ['runtime-triggers'] });
  };
};

/** The per-definition actions of the definitions card: enable/disable, run now, and delete. */
export const useTriggerDefinitionActions = (onRun: (definitionId: number) => void) => {
  const refresh = useRefreshTriggerDefinitions();
  const toggle = useMutation({
    mutationFn: (definition: TriggerDefinition) => triggerDefinitionApi.update(definition.id, { enabled: !definition.enabled }),
    onSuccess: refresh,
  });
  const run = useMutation({
    mutationFn: (id: number) => triggerDefinitionApi.run(id),
    onSuccess: (_response, id) => {
      onRun(id);
      refresh();
    },
  });
  const remove = useMutation({ mutationFn: (id: number) => triggerDefinitionApi.delete(id), onSuccess: refresh });
  return { toggle, run, remove };
};
