import React, { useCallback, useState } from 'react';
import { ConfirmDialog } from '../components/ui/ConfirmDialog';

interface ConfirmRequest {
  title: string;
  message: React.ReactNode;
  confirmLabel?: string;
  tone?: 'default' | 'warning';
  /** Runs only when the user confirms; Cancel, Escape and the close button drop it. */
  onConfirm: () => void;
}

/**
 * Drop-in for `if (window.confirm(...)) doIt()` (JAN-292): call
 * `confirm({ title, message, onConfirm: doIt })` and render `dialog` once
 * anywhere in the component. One request is open at a time.
 */
export function useConfirmDialog() {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const confirm = useCallback((next: ConfirmRequest) => setRequest(next), []);
  const cancel = useCallback(() => setRequest(null), []);

  const dialog = (
    <ConfirmDialog
      isOpen={request !== null}
      title={request?.title ?? ''}
      message={request?.message ?? null}
      confirmLabel={request?.confirmLabel}
      tone={request?.tone}
      onConfirm={() => {
        const current = request;
        setRequest(null);
        current?.onConfirm();
      }}
      onCancel={cancel}
    />
  );

  return { confirm, dialog };
}
