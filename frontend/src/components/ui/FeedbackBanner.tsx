import React from 'react';
import { AlertCircle, Check } from 'lucide-react';

export interface Feedback {
  status: 'success' | 'error';
  message: string;
}

/** The success or error line a settings card shows after saving or testing; nothing without feedback. */
export const FeedbackBanner: React.FC<{ feedback: Feedback | null }> = ({ feedback }) => {
  if (!feedback) return null;
  const success = feedback.status === 'success';
  return (
    <div
      role={success ? 'status' : 'alert'}
      className={`p-4 rounded-2xl text-sm flex items-center gap-3 ${
        success
          ? 'bg-green-50 text-green-600 dark:bg-green-900/20 dark:text-green-400'
          : 'bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400'
      }`}
    >
      {success ? <Check size={20} /> : <AlertCircle size={20} />}
      <p className="font-medium">{feedback.message}</p>
    </div>
  );
};
