import React from 'react';
import { Check, Sparkles, X } from 'lucide-react';
import type { CharacterStateProposal } from '../../../lib/characterState';
import { Button } from '../../ui/Button';
import { Card, CardContent, CardHeader } from '../../ui/Card';
import { describeValues } from './characterStateShared';

interface CharacterStateProposalsProps {
  proposals: CharacterStateProposal[];
  busyId: number | null;
  onApprove: (id: number) => void;
  onReject: (id: number) => void;
}

/** Janus's open proposals; nothing changes until the owner approves one. All text renders as plain text. */
export const CharacterStateProposals: React.FC<CharacterStateProposalsProps> = ({ proposals, busyId, onApprove, onReject }) => (
  <Card>
    <CardHeader>
      <h3 className="font-bold flex items-center gap-2">
        <Sparkles size={16} className="text-primary" />
        Proposed by Janus
      </h3>
      <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
        Janus proposes changes from chat; they apply only when you approve them, and only within your bounds.
      </p>
    </CardHeader>
    <CardContent className="space-y-3">
      {proposals.length === 0 ? (
        <p className="text-sm text-gray-500 dark:text-gray-400">No open proposals.</p>
      ) : (
        proposals.map((proposal) => (
          <article
            key={proposal.id}
            aria-label={`Proposal ${proposal.id}`}
            className="rounded-xl border border-gray-200 dark:border-gray-700 p-3 space-y-2"
          >
            <p className="text-sm font-medium" data-testid="character-proposal-changes">
              {describeValues(proposal.changes)}
            </p>
            <p className="text-xs text-gray-500 dark:text-gray-400" data-testid="character-proposal-rationale">
              {proposal.rationale}
            </p>
            <div className="flex gap-2">
              <Button
                size="sm"
                icon={<Check size={14} />}
                isLoading={busyId === proposal.id}
                onClick={() => onApprove(proposal.id)}
              >
                Approve
              </Button>
              <Button size="sm" variant="outline" icon={<X size={14} />} disabled={busyId === proposal.id} onClick={() => onReject(proposal.id)}>
                Reject
              </Button>
            </div>
          </article>
        ))
      )}
    </CardContent>
  </Card>
);
