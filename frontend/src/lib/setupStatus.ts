import { isAxiosError } from 'axios';
import { authApi } from '../api';

export const SETUP_STATUS_QUERY_KEY = ['setup-status'] as const;

/**
 * Setup status only changes once per install, so "no setup needed" is fetched once. The backend answers
 * 404 when public setup is disabled (the default); that must resolve to data, not an error:
 * a data-less query goes back to `isLoading` on every refetch (e.g. window refocus), which
 * would swap the whole app shell for the loader and wipe every page's local state (JAN-182).
 * "Setup needed" stays refreshable on focus, so a tab left on /setup notices when another tab
 * or the CLI creates the first admin; that refetch keeps its data, so it never shows the loader.
 */
export const setupStatusQueryOptions = {
  queryKey: SETUP_STATUS_QUERY_KEY,
  queryFn: async (): Promise<{ needs_setup: boolean }> => {
    try {
      return (await authApi.getSetupStatus()).data;
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 404) {
        return { needs_setup: false };
      }
      throw error;
    }
  },
  retry: false,
  staleTime: Infinity,
  refetchOnWindowFocus: (query: { state: { data?: { needs_setup: boolean } } }) =>
    query.state.data?.needs_setup ? ('always' as const) : false,
};
