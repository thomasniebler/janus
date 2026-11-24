import React from 'react';
import { AlertTriangle } from 'lucide-react';
import { Button } from './Button';
import { Modal } from './Modal';

interface ConfirmDialogProps {
  isOpen: boolean;
  title: string;
  /** The question or warning; plain text or richer markup. */
  message: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** `warning` marks a risky action: amber icon, and the confirm button uses the danger style. */
  tone?: 'default' | 'warning';
  onConfirm: () => void;
  /** Also fired by Escape and the dialog's close button. */
  onCancel: () => void;
}

/**
 * In-app replacement for `window.confirm` (JAN-275): a `Modal` with a message
 * and Cancel / confirm buttons. Cancel takes focus, so pressing Enter right
 * away never confirms a risky action by accident.
 */
export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  isOpen,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  tone = 'default',
  onConfirm,
  onCancel,
}) => (
  <Modal isOpen={isOpen} onClose={onCancel} title={title}>
    <div className="space-y-5">
      <div className="flex gap-3 text-sm text-gray-600 dark:text-gray-300">
        {tone === 'warning' && <AlertTriangle size={20} className="mt-0.5 shrink-0 text-amber-400" aria-hidden="true" />}
        <div className="min-w-0 break-words">{message}</div>
      </div>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" size="sm" onClick={onCancel} autoFocus>
          {cancelLabel}
        </Button>
        <Button type="button" variant={tone === 'warning' ? 'danger' : 'primary'} size="sm" onClick={onConfirm}>
          {confirmLabel}
        </Button>
      </div>
    </div>
  </Modal>
);
