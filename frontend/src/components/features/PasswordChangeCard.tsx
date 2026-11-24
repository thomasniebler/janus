import React, { useMemo, useState } from 'react';
import axios from 'axios';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { AlertCircle, CheckCircle2, KeyRound } from 'lucide-react';
import { authApi } from '../../api';
import { Button } from '../ui/Button';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Input } from '../ui/Input';

type PasswordForm = {
  currentPassword: string;
  newPassword: string;
  confirmPassword: string;
};

// bcrypt only covers 72 UTF-8 bytes; the backend rejects longer new passwords.
const MAX_PASSWORD_BYTES = 72;

const EMPTY_FORM: PasswordForm = {
  currentPassword: '',
  newPassword: '',
  confirmPassword: '',
};

type PasswordChangeCardProps = {
  /** Pre-fills the form; lets stories render the filled/validation states. */
  initialForm?: Partial<PasswordForm>;
};

export const PasswordChangeCard: React.FC<PasswordChangeCardProps> = ({ initialForm }) => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<PasswordForm>({ ...EMPTY_FORM, ...initialForm });
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const validationMessage = useMemo(() => {
    if (new TextEncoder().encode(form.newPassword).length > MAX_PASSWORD_BYTES) {
      return `New password must be at most ${MAX_PASSWORD_BYTES} bytes long.`;
    }
    if (!form.confirmPassword) {
      return null;
    }
    if (form.newPassword !== form.confirmPassword) {
      return 'New passwords do not match.';
    }
    return null;
  }, [form.confirmPassword, form.newPassword]);

  const changePasswordMutation = useMutation({
    mutationFn: () =>
      authApi.changePassword({
        current_password: form.currentPassword,
        new_password: form.newPassword,
      }),
    onSuccess: async () => {
      queryClient.removeQueries({ queryKey: ['me'] });
      navigate('/login?changed=password', { replace: true });
    },
    onError: (error) => {
      // Validation errors (422) carry a list in `detail`; only a string is safe to render.
      const detail = axios.isAxiosError<{ detail?: unknown }>(error) ? error.response?.data?.detail : undefined;
      setErrorMessage(typeof detail === 'string' ? detail : 'Failed to update password.');
    },
  });

  const updateField = <K extends keyof PasswordForm>(field: K, value: PasswordForm[K]) => {
    setForm((current) => ({ ...current, [field]: value }));
    setErrorMessage(null);
  };

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (validationMessage) {
      return;
    }
    setErrorMessage(null);
    changePasswordMutation.mutate();
  };

  const isSubmitDisabled =
    changePasswordMutation.isPending ||
    !form.currentPassword ||
    !form.newPassword ||
    !form.confirmPassword ||
    Boolean(validationMessage);

  return (
    <Card>
      <CardHeader>
        <div className="space-y-2">
          <h2 className="text-xl font-bold flex items-center gap-2">
            <KeyRound size={22} className="text-primary" />
            Password & Session Security
          </h2>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Confirm your current password, then enter the new password twice. Saving will invalidate your current
            session, and you will sign in again with the new password.
          </p>
        </div>
      </CardHeader>

      <CardContent className="space-y-5">
        <div className="rounded-xl border border-dashed border-gray-200 dark:border-gray-700 p-4 text-sm text-gray-600 dark:text-gray-300">
          <div className="flex items-center gap-2 font-bold mb-2">
            <CheckCircle2 size={16} className="text-primary" />
            What happens after saving
          </div>
          <p>Your active browser session and any other existing sessions will be invalidated immediately.</p>
        </div>

        {validationMessage && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-200">
            <div className="flex items-center gap-2 font-bold">
              <AlertCircle size={16} />
              Check your new password
            </div>
            <p className="mt-2">{validationMessage}</p>
          </div>
        )}

        {errorMessage && (
          <div className="rounded-xl border border-red-100 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-300">
            <div className="flex items-center gap-2 font-bold">
              <AlertCircle size={16} />
              Password update failed
            </div>
            <p className="mt-2">{errorMessage}</p>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <Input
            label="Current Password"
            type="password"
            autoComplete="current-password"
            required
            value={form.currentPassword}
            onChange={(event) => updateField('currentPassword', event.target.value)}
          />
          <div className="grid gap-4 md:grid-cols-2">
            <Input
              label="New Password"
              type="password"
              autoComplete="new-password"
              required
              value={form.newPassword}
              onChange={(event) => updateField('newPassword', event.target.value)}
              error={validationMessage ?? undefined}
            />
            <Input
              label="Repeat New Password"
              type="password"
              autoComplete="new-password"
              required
              value={form.confirmPassword}
              onChange={(event) => updateField('confirmPassword', event.target.value)}
              error={validationMessage ?? undefined}
            />
          </div>

          <div className="flex flex-col sm:flex-row gap-3">
            <Button type="submit" isLoading={changePasswordMutation.isPending} disabled={isSubmitDisabled}>
              Update Password
            </Button>
            <Button type="button" variant="secondary" onClick={() => {
              setForm(EMPTY_FORM);
              setErrorMessage(null);
            }}>
              Clear
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
};
