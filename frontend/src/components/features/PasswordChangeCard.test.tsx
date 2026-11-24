import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AxiosError, AxiosHeaders } from 'axios';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { authApi } from '../../api';
import { PasswordChangeCard } from './PasswordChangeCard';

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const renderCard = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <PasswordChangeCard />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

const fillForm = async (newPassword: string) => {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText('Current Password'), 'old-secret');
  await user.type(screen.getByLabelText('New Password'), newPassword);
  await user.type(screen.getByLabelText('Repeat New Password'), newPassword);
  return user;
};

describe('PasswordChangeCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('blocks new passwords longer than bcrypt’s 72-byte limit', async () => {
    renderCard();
    // 37 two-byte characters = 74 UTF-8 bytes, though only 37 characters.
    await fillForm('é'.repeat(37));

    expect(screen.getAllByText(/at most 72 bytes/i).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /update password/i })).toBeDisabled();
    expect(authApi.changePassword).not.toHaveBeenCalled();
  });

  it('falls back to a generic message when the error detail is not a string', async () => {
    const response = {
      status: 422,
      statusText: 'Unprocessable Entity',
      headers: {},
      config: { headers: new AxiosHeaders() },
      data: { detail: [{ loc: ['body', 'new_password'], msg: 'Value error' }] },
    };
    vi.mocked(authApi.changePassword).mockRejectedValue(
      new AxiosError('Request failed', '422', response.config, null, response)
    );
    renderCard();
    const user = await fillForm('new-secret');

    await user.click(screen.getByRole('button', { name: /update password/i }));

    await waitFor(() => expect(screen.getByText('Failed to update password.')).toBeInTheDocument());
  });
});
