import React from 'react';
import { Inbox } from 'lucide-react';
import { Card } from '../ui/Card';
import { EmailItem } from './EmailItem';
import type { Email } from '../../types';

interface EmailListProps {
  emails: Email[];
  isLoading?: boolean;
  onOpenEmail?: (emailId: number) => void;
}

export const EmailList: React.FC<EmailListProps> = ({ emails, isLoading, onOpenEmail }) => {
  if (isLoading) {
    return (
      <Card className="divide-y divide-gray-100 dark:divide-gray-800 overflow-hidden">
        {Array.from({ length: 5 }).map((_, index) => (
          <div key={index} className="p-5 animate-pulse">
            <div className="flex items-start gap-4">
              <div className="w-10 h-10 rounded-lg bg-gray-100 dark:bg-gray-800" />
              <div className="flex-1 space-y-3">
                <div className="flex justify-between gap-4">
                  <div className="h-4 w-32 rounded bg-gray-100 dark:bg-gray-800" />
                  <div className="h-4 w-20 rounded bg-gray-100 dark:bg-gray-800" />
                </div>
                <div className="h-4 w-2/3 rounded bg-gray-100 dark:bg-gray-800" />
                <div className="h-3 w-full rounded bg-gray-100 dark:bg-gray-800" />
              </div>
            </div>
          </div>
        ))}
      </Card>
    );
  }

  if (emails.length === 0) {
    return (
      <div className="p-20 text-center bg-white dark:bg-dark-card rounded-2xl border border-gray-100 dark:border-gray-800">
        <div className="bg-gray-50 dark:bg-gray-800 w-20 h-20 rounded-full flex items-center justify-center mx-auto mb-6">
          <Inbox className="text-gray-300 dark:text-gray-600" size={40} />
        </div>
        <h3 className="text-lg font-bold mb-2">No emails found</h3>
        <p className="text-gray-500 dark:text-gray-400 max-w-xs mx-auto">
          No emails yet. Add an IMAP account in Settings or adjust your current filter.
        </p>
      </div>
    );
  }

  return (
    <Card className="divide-y divide-gray-100 dark:divide-gray-800 overflow-hidden">
      {emails.map((email) => (
        <EmailItem key={email.id} email={email} onOpenEmail={onOpenEmail} />
      ))}
    </Card>
  );
};
