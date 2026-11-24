import React, { useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { characterStateApi } from '../../../lib/characterState';
import type { CharacterStateView } from '../../../lib/characterState';
import { Card } from '../../ui/Card';
import { FeedbackBanner, type Feedback } from '../../ui/FeedbackBanner';
import { CharacterStateBounds } from './CharacterStateBounds';
import { CharacterStateEditor } from './CharacterStateEditor';
import { CharacterStateHistory } from './CharacterStateHistory';
import { CharacterStateProposals } from './CharacterStateProposals';
import {
  characterStateHistoryQueryKey,
  characterStateProposalsQueryKey,
  characterStateQueryKey,
  errorMessage,
} from './characterStateShared';

type Busy = { kind: 'proposal' | 'restore'; id: number } | null;

/**
 * Character controls for the bounded adaptive state (JAN-301): the current state, Janus's
 * open proposals, the owner's bounds, and the audited history with restore and reset.
 */
export const CharacterStateManager: React.FC = () => {
  const queryClient = useQueryClient();
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [busy, setBusy] = useState<Busy>(null);

  const stateQuery = useQuery({ queryKey: characterStateQueryKey, queryFn: async () => (await characterStateApi.get()).data });
  const historyQuery = useInfiniteQuery({
    queryKey: characterStateHistoryQueryKey,
    queryFn: async ({ pageParam }) => (await characterStateApi.history(pageParam)).data,
    initialPageParam: null as number | null,
    getNextPageParam: (page) => page.next_before_id,
  });
  const proposalsQuery = useQuery({
    queryKey: characterStateProposalsQueryKey,
    queryFn: async () => (await characterStateApi.proposals()).data,
  });

  const run = useMutation({
    mutationFn: async ({ call }: { call: () => Promise<unknown>; success: string; fallback: string }) => call(),
    onSuccess: (response, { success }) => {
      const data = (response as { data?: unknown } | undefined)?.data as Partial<CharacterStateView> | undefined;
      if (data && 'state' in data) queryClient.setQueryData(characterStateQueryKey, data);
      setFeedback({ status: 'success', message: success });
    },
    onError: (error, { fallback }) => setFeedback({ status: 'error', message: errorMessage(error, fallback) }),
    onSettled: () => {
      setBusy(null);
      void queryClient.invalidateQueries({ queryKey: characterStateQueryKey });
      void queryClient.invalidateQueries({ queryKey: characterStateHistoryQueryKey });
      void queryClient.invalidateQueries({ queryKey: characterStateProposalsQueryKey });
    },
  });

  const view = stateQuery.data;
  if (!view) {
    return (
      <Card className="h-32 flex items-center justify-center">
        {stateQuery.isError ? (
          <p className="text-sm text-red-300">Could not load the adaptive state.</p>
        ) : (
          <RefreshCw className="animate-spin text-primary" />
        )}
      </Card>
    );
  }

  const act = (call: () => Promise<unknown>, success: string, fallback: string, nextBusy: Busy = null) => {
    setBusy(nextBusy);
    setFeedback(null);
    run.mutate({ call, success, fallback });
  };
  const pending = (proposals: typeof proposalsQuery.data) => (proposals ?? []).filter((item) => item.status === 'proposed');

  return (
    <section className="space-y-6" aria-labelledby="character-state-heading">
      <div>
        <h2 id="character-state-heading" className="text-xl font-bold">
          Adaptive state
        </h2>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Tone, verbosity, proactivity and focus Janus uses on top of the constitution, in chat and every trigger run.
        </p>
      </div>
      <FeedbackBanner feedback={feedback} />
      <CharacterStateProposals
        proposals={pending(proposalsQuery.data)}
        busyId={busy?.kind === 'proposal' ? busy.id : null}
        onApprove={(id) =>
          act(() => characterStateApi.approve(id), 'Proposal approved.', 'Could not approve the proposal.', { kind: 'proposal', id })
        }
        onReject={(id) =>
          act(() => characterStateApi.reject(id), 'Proposal rejected.', 'Could not reject the proposal.', { kind: 'proposal', id })
        }
      />
      <CharacterStateEditor
        key={`state-${view.revision}`}
        view={view}
        pending={run.isPending}
        onSave={(values) => act(() => characterStateApi.update(values), 'Adaptive state saved.', 'Could not save the state.')}
        onReset={() => act(() => characterStateApi.reset(), 'Reset to the defaults.', 'Could not reset the state.')}
      />
      <CharacterStateHistory
        changes={historyQuery.data?.pages.flatMap((page) => page.items) ?? []}
        hasMore={historyQuery.hasNextPage}
        loadingMore={historyQuery.isFetchingNextPage}
        loadError={historyQuery.isError}
        onLoadMore={() => void historyQuery.fetchNextPage()}
        currentRevision={view.revision}
        busyId={busy?.kind === 'restore' ? busy.id : null}
        onRestore={(id) =>
          act(() => characterStateApi.rollback(id), 'Earlier state restored.', 'Could not restore that state.', { kind: 'restore', id })
        }
      />
      <CharacterStateBounds
        key={`bounds-${JSON.stringify(view.envelope)}`}
        envelope={view.envelope}
        pending={run.isPending}
        onSave={(envelope) =>
          act(() => characterStateApi.updateEnvelope(envelope), 'Bounds saved.', 'Could not save the bounds.')
        }
      />
    </section>
  );
};
