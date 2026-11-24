import { useQuery } from '@tanstack/react-query';
import { triggerDefinitionApi } from '../../../api';

/**
 * Suggested automations (JAN-235). Kept apart from `triggerDefinitionQueries`
 * because every shell imports it (for the navigation badge): under vitest's
 * `isolate: false`, a module shared by several test files stays bound to the
 * first file's API mock, so shell tests seed this query's cache instead.
 */
export const triggerProposalQueryKey = ['trigger-proposals'] as const;
export const triggerProposalCountQueryKey = [...triggerProposalQueryKey, 'count'] as const;

/** How many suggested automations await review; 0 while loading or on error. */
export const useOpenProposalCount = (): number => {
  const { data } = useQuery({
    queryKey: triggerProposalCountQueryKey,
    queryFn: async () => (await triggerDefinitionApi.proposalCount()).data,
    refetchInterval: 30000,
  });
  return data?.open ?? 0;
};
