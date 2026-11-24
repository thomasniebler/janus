import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AxiosError, AxiosHeaders } from 'axios';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { authApi } from '../api';
import Setup from './Setup';

vi.mock('../api', async (importOriginal) => {
  const { mockApiModule } = await import('../test/apiMock');
  return mockApiModule(importOriginal);
});

const rejectWith = (status: number, detail: unknown) => {
  const response = {
    status,
    statusText: 'Error',
    headers: {},
    config: { headers: new AxiosHeaders() },
    data: { detail },
  };
  vi.mocked(authApi.setup).mockRejectedValue(
    new AxiosError(`Request failed with status code ${status}`, String(status), response.config, null, response)
  );
};

const submitForm = async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    </QueryClientProvider>
  );
  const user = userEvent.setup();
  await user.type(screen.getByLabelText('Setup Token'), 'wrong-token');
  await user.type(screen.getByLabelText('Username'), 'owner');
  await user.type(screen.getByLabelText('Password', { exact: true }), 'owner-password-1234');
  await user.type(screen.getByLabelText('Confirm Password'), 'owner-password-1234');
  await user.click(screen.getByRole('button', { name: 'Create Admin Account' }));
};

describe('Setup', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // Regression (JAN-218): a wrong token used to show axios's
  // "Request failed with status code 400" instead of the backend's reason.
  it('shows the backend reason when the setup token is refused', async () => {
    rejectWith(400, 'Setup token is invalid or has expired.');
    await submitForm();

    expect(await screen.findByText('Setup token is invalid or has expired.')).toBeInTheDocument();
    expect(screen.queryByText(/status code 400/)).not.toBeInTheDocument();
  });

  it('falls back to a generic message when the error detail is not a string', async () => {
    rejectWith(422, [{ loc: ['body', 'username'], msg: 'Value error' }]);
    await submitForm();

    expect(await screen.findByText('Setup failed.')).toBeInTheDocument();
  });
});
