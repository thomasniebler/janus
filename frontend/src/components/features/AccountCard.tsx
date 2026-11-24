import React from 'react';
import { Mail, Trash2, Check, AlertCircle, Pencil } from 'lucide-react';
import { Card } from '../ui/Card';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
import type { EmailAccount } from '../../types';

interface AccountCardProps {
  account: EmailAccount;
  testResult?: { status: string; message: string };
  isTesting: boolean;
  isDeleting: boolean;
  onEdit: () => void;
  onTest: () => void;
  onDelete: () => void;
}

/** One mailbox: its name and address, edit (password accounts only), connection test and removal. */
export const AccountCard: React.FC<AccountCardProps> = ({
  account,
  testResult,
  isTesting,
  isDeleting,
  onEdit,
  onTest,
  onDelete,
}) => {
  const usesPassword = !account.auth_type || account.auth_type === 'password';
  return (
    <Card className="p-4 flex flex-col gap-4">
      <div className="flex justify-between items-center">
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 rounded-xl bg-primary/10 text-primary flex items-center justify-center">
            <Mail size={24} />
          </div>
          <div>
            <h3 className="font-bold flex items-center gap-2">
              {account.name}
              {!usesPassword && <Badge variant="secondary" className="text-xs capitalize">{account.auth_type}</Badge>}
            </h3>
            <p className="text-xs text-gray-500 dark:text-gray-400">{account.email}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {usesPassword && (
            <Button variant="ghost" size="sm" onClick={onEdit} icon={<Pencil size={16} />}>
              Edit
            </Button>
          )}
          <Button variant="secondary" size="sm" onClick={onTest} isLoading={isTesting}>
            Test Connection
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onDelete}
            isLoading={isDeleting}
            icon={<Trash2 size={18} className="text-gray-400 hover:text-accent" />}
          />
        </div>
      </div>

      {testResult && (
        <div className={`p-3 rounded-xl text-xs flex items-center gap-2 ${testResult.status === 'success' ? 'bg-green-50 text-green-600 dark:bg-green-900/20 dark:text-green-400' : 'bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400'}`}>
          {testResult.status === 'success' ? <Check size={14} /> : <AlertCircle size={14} />}
          {testResult.message}
        </div>
      )}
    </Card>
  );
};
