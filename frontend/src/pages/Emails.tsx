import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { emailApi } from '../api';
import { AccountManager } from '../components/features/AccountManager';
import { EmailDetailView } from '../components/features/EmailDetailView';
import { EmailList } from '../components/features/EmailList';
import { ErrorBoundary } from '../components/ui/ErrorBoundary';
import { emailPageItem } from '../hooks/pageContextSections';
import { usePageContext } from '../hooks/usePageContext';

const PRIORITY_FILTERS = ['all', 'important', 'digest', 'spam'] as const;
type PriorityFilter = (typeof PRIORITY_FILTERS)[number];

const Emails: React.FC = () => {
  const [filter, setFilter] = useState<PriorityFilter>('all');
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  // The inbox list never holds spam, so the spam filter reads its own folder (JAN-219).
  const folder = filter === 'spam' ? 'spam' : 'inbox';
  const { data: emails, isLoading } = useQuery({
    queryKey: ['emails-list', folder],
    queryFn: async () => (await emailApi.listEmails(100, folder === 'spam' ? 'spam' : undefined)).data,
    refetchInterval: 10000,
  });

  const filteredEmails = (emails || []).filter(e => {
    if (filter === 'all') return true;
    return e.priority === filter;
  });

  const selectedEmailIdParam = searchParams.get('email');
  const selectedEmailId = selectedEmailIdParam ? Number.parseInt(selectedEmailIdParam, 10) : null;
  const isValidSelectedEmailId = selectedEmailId !== null && !Number.isNaN(selectedEmailId);
  const selectedEmail = isValidSelectedEmailId
    ? (emails || []).find((email) => email.id === selectedEmailId)
    : undefined;

  const openEmail = (emailId: number) => {
    navigate(`/emails?email=${emailId}`);
  };

  const closeEmail = () => {
    navigate('/emails');
  };

  // Janus sees the active filter, the listed emails, and the open one (JAN-250), and
  // may set the filter, open an email (undo: back to what was open), or highlight one (JAN-251).
  usePageContext(
    {
      page: 'Emails',
      title: 'Email Intelligence',
      filters: { priority: filter },
      selection: selectedEmail ? emailPageItem(selectedEmail) : null,
      visible_items: filteredEmails.map(emailPageItem),
    },
    {
      page: 'emails',
      path: '/emails',
      filters: { priority: { values: PRIORITY_FILTERS, get: () => filter, set: (value) => setFilter(value as PriorityFilter) } },
      items: {
        email: {
          open: (id) => {
            const previous = isValidSelectedEmailId ? selectedEmailId : null;
            openEmail(Number(id));
            return () => (previous === null ? closeEmail() : openEmail(previous));
          },
          highlight: true,
        },
      },
    }
  );

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Email Intelligence</h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">AI-classified communications from your accounts.</p>
        </div>
        <div className="flex bg-white dark:bg-dark-card p-1 rounded-xl border border-gray-100 dark:border-gray-800 shadow-sm uiv2:border-v2-border uiv2:bg-v2-surface uiv2:p-0 uiv3:border-v3-border uiv3:bg-v3-surface uiv3:p-0">
          {PRIORITY_FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-4 py-1.5 rounded-lg text-xs font-bold capitalize transition-all uiv2:font-terminal uiv2:text-[10px] uiv2:uppercase uiv2:tracking-[0.18em] uiv3:font-typewriter uiv3:text-[10px] uiv3:uppercase uiv3:tracking-[0.16em] ${filter === f ? 'bg-primary text-white shadow-md shadow-primary/20 uiv2:bg-v2-accent uiv2:text-v2-bg uiv2:shadow-[0_0_14px_0_rgba(198,242,74,0.4)] uiv3:bg-v3-accent uiv3:text-[#1a0b07] uiv3:shadow-none' :'text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-800 uiv2:text-v2-text-muted uiv2:hover:bg-v2-accent-soft uiv2:hover:text-v2-accent uiv3:text-v3-text-muted uiv3:hover:bg-v3-accent-soft uiv3:hover:text-v3-accent-bright'}`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      <ErrorBoundary title="The mailbox setup crashed">
        <AccountManager />
      </ErrorBoundary>

      <ErrorBoundary title="The email list crashed">
        <div className="space-y-4">
          {selectedEmail && <EmailDetailView email={selectedEmail} onClose={closeEmail} />}

          {isValidSelectedEmailId && !selectedEmail && !isLoading && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 dark:bg-amber-900/20 dark:border-amber-800 p-4 text-sm text-amber-700 dark:text-amber-300">
              The selected email is no longer available in the current list.
            </div>
          )}

          <EmailList emails={filteredEmails} isLoading={isLoading} onOpenEmail={openEmail} />
        </div>
      </ErrorBoundary>
    </div>
  );
};

export default Emails;
