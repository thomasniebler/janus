import React, { useState } from 'react';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { AlertCircle, KeyRound, Lock, User } from 'lucide-react';
import { authApi } from '../api';
import { SETUP_STATUS_QUERY_KEY } from '../lib/setupStatus';

// bcrypt only covers 72 UTF-8 bytes; the backend rejects longer passwords.
const MAX_PASSWORD_BYTES = 72;

const Setup: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [token, setToken] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    if (new TextEncoder().encode(password).length > MAX_PASSWORD_BYTES) {
      setError(`Password must be at most ${MAX_PASSWORD_BYTES} bytes long.`);
      return;
    }

    setError(null);
    setIsLoading(true);

    try {
      await authApi.setup({ token, username, password });
      // The router caches setup status indefinitely; without this it would bounce /login back to /setup.
      queryClient.setQueryData(SETUP_STATUS_QUERY_KEY, { needs_setup: false });
      navigate('/login', { replace: true });
    } catch (err) {
      // Show the backend's reason (wrong or expired token, setup already done, ...) rather
      // than axios's generic "Request failed with status code 400" (JAN-218). Validation
      // errors (422) carry a list in `detail`; only a string is safe to render.
      const detail = axios.isAxiosError<{ detail?: unknown }>(err) ? err.response?.data?.detail : undefined;
      setError(typeof detail === 'string' ? detail : 'Setup failed.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-light-bg dark:bg-dark-bg p-4">
      <div className="max-w-md w-full space-y-8">
        <div className="text-center space-y-2">
          <div className="inline-flex items-center justify-center w-20 h-20 rounded-2xl bg-white dark:bg-dark-card shadow-xl shadow-primary/10">
            <span className="text-4xl">🦉</span>
          </div>
          <h1 className="text-3xl font-extrabold tracking-tight">Set up Janus</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Paste the one-time token printed by the backend and create the first admin account.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="bg-white dark:bg-dark-card rounded-2xl shadow-xl border border-gray-100 dark:border-gray-800 p-8 space-y-5">
          {error && (
            <div className="bg-red-50 dark:bg-red-900/20 border border-red-100 dark:border-red-800/50 rounded-lg p-3 flex items-center gap-2 text-red-600 dark:text-red-400 text-sm">
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
          )}

          <label className="block text-xs font-bold text-gray-400 uppercase tracking-wider">
            Setup Token
            <div className="relative mt-1">
              <KeyRound size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                className="block w-full pl-10 pr-3 py-3 border border-gray-200 dark:border-gray-700 rounded-xl bg-gray-50 dark:bg-dark-bg text-sm outline-none"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                required
              />
            </div>
          </label>

          <label className="block text-xs font-bold text-gray-400 uppercase tracking-wider">
            Username
            <div className="relative mt-1">
              <User size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                className="block w-full pl-10 pr-3 py-3 border border-gray-200 dark:border-gray-700 rounded-xl bg-gray-50 dark:bg-dark-bg text-sm outline-none"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                required
              />
            </div>
          </label>

          <label className="block text-xs font-bold text-gray-400 uppercase tracking-wider">
            Password
            <div className="relative mt-1">
              <Lock size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                type="password"
                className="block w-full pl-10 pr-3 py-3 border border-gray-200 dark:border-gray-700 rounded-xl bg-gray-50 dark:bg-dark-bg text-sm outline-none"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </div>
          </label>

          <label className="block text-xs font-bold text-gray-400 uppercase tracking-wider">
            Confirm Password
            <div className="relative mt-1">
              <Lock size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                type="password"
                className="block w-full pl-10 pr-3 py-3 border border-gray-200 dark:border-gray-700 rounded-xl bg-gray-50 dark:bg-dark-bg text-sm outline-none"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                required
              />
            </div>
          </label>

          <button
            type="submit"
            disabled={isLoading}
            className="w-full flex justify-center py-3 px-4 rounded-xl text-sm font-bold text-white bg-primary hover:bg-blue-600 disabled:opacity-50"
          >
            {isLoading ? 'Creating admin...' : 'Create Admin Account'}
          </button>
        </form>
      </div>
    </div>
  );
};

export default Setup;
