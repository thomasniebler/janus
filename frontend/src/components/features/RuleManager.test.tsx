import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError, AxiosHeaders, type AxiosResponse } from 'axios';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { emailApi } from '../../api';
import { RuleManager } from './RuleManager';

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const rejection = (status: number, detail: unknown) =>
  new AxiosError('Request failed', String(status), undefined, undefined, {
    status,
    statusText: '',
    headers: {},
    config: { headers: new AxiosHeaders() },
    data: { detail },
  } as AxiosResponse);

const renderManager = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RuleManager />
    </QueryClientProvider>
  );
};

const submitRule = async (pattern: string) => {
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: 'New Rule' }));
  const dialog = screen.getByRole('dialog', { name: 'Add New Rule' });
  await user.type(within(dialog).getByLabelText('Rule Name'), 'Broken');
  await user.type(within(dialog).getByLabelText('Pattern (Regex or Keyword)'), pattern);
  await user.click(within(dialog).getByRole('button', { name: 'Add Rule' }));
  return { user, dialog };
};

describe('RuleManager', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(emailApi.getRules).mockResolvedValue({ data: [] } as unknown as AxiosResponse);
  });

  // JAN-219: a refused rule used to leave the form open without saying why.
  it('shows the backend reason when a rule is refused and clears it on close', async () => {
    vi.mocked(emailApi.addRule).mockRejectedValue(rejection(400, 'Invalid regex pattern: missing ), unterminated subpattern'));
    renderManager();

    const { user, dialog } = await submitRule('(unclosed');

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Invalid regex pattern');
    expect(screen.getByRole('dialog', { name: 'Add New Rule' })).toBeInTheDocument();

    await user.keyboard('{Escape}');
    await user.click(await screen.findByRole('button', { name: 'New Rule' }));
    expect(within(screen.getByRole('dialog', { name: 'Add New Rule' })).queryByRole('alert')).not.toBeInTheDocument();
  });

  it('falls back to a generic message for a validation error list', async () => {
    vi.mocked(emailApi.addRule).mockRejectedValue(rejection(422, [{ loc: ['body', 'name'], msg: 'Field required' }]));
    renderManager();

    const { dialog } = await submitRule('x');

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Could not save the rule.');
  });
});
