import { useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosResponse } from 'axios';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { plannerApi } from '../../api';
import type { LLMModelListResponse, LLMProviderId } from '../../types';
import { LLMModelSelect, MODEL_LIST_FALLBACK_NOTE } from './LLMModelSelect';

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const response = <T,>(data: T): AxiosResponse<T> => ({ data }) as AxiosResponse<T>;

const Harness = ({
  provider = 'anthropic',
  initialValue = '',
  apiKey = 'sk-ant-draft',
  baseUrl,
}: {
  provider?: LLMProviderId;
  initialValue?: string;
  apiKey?: string;
  baseUrl?: string;
}) => {
  const [value, setValue] = useState(initialValue);
  return (
    <>
      <LLMModelSelect
        provider={provider}
        label="Anthropic Model"
        value={value}
        onChange={setValue}
        placeholder="claude-sonnet-5"
        apiKey={apiKey}
        baseUrl={baseUrl}
      />
      <span data-testid="value">{value}</span>
    </>
  );
};

const renderSelect = (props: Parameters<typeof Harness>[0] = {}) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <Harness {...props} />
    </QueryClientProvider>
  );
};

describe('LLMModelSelect', () => {
  // `vitest.config.ts` runs with `isolate: false`, and `apiMock.ts` shares one `vi.fn()`
  // per API method across every test file in a worker. Clearing at the *start* of each
  // test (not just this file's own afterEach) guards against a mock implementation left
  // behind by whichever file the runner happened to execute beforehand.
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows a loading state while the model list is fetched', async () => {
    let resolve: (value: AxiosResponse<LLMModelListResponse>) => void = () => undefined;
    vi.mocked(plannerApi.listLLMModels).mockReturnValue(new Promise((done) => (resolve = done)));

    renderSelect();

    expect(screen.getByRole('status')).toHaveTextContent(/loading available models/i);
    resolve(response({ models: [{ id: 'claude-sonnet-5', label: 'Claude Sonnet 5' }] }));
    expect(await screen.findByText(/1 model available/i)).toBeInTheDocument();
  });

  it('queries with the draft key and base URL, then lets the user search and pick a model', async () => {
    vi.mocked(plannerApi.listLLMModels).mockResolvedValue(
      response({
        models: [
          { id: 'claude-haiku-4-5', label: 'Claude Haiku 4.5' },
          { id: 'claude-opus-5', label: 'Claude Opus 5' },
          { id: 'claude-sonnet-5', label: 'Claude Sonnet 5' },
        ],
      })
    );

    renderSelect({ baseUrl: 'https://anthropic-proxy.internal' });
    const user = userEvent.setup();

    expect(await screen.findByText(/3 models available/i)).toBeInTheDocument();
    const combobox = screen.getByRole('combobox', { name: /anthropic model/i });
    expect(plannerApi.listLLMModels).toHaveBeenCalledWith({
      provider: 'anthropic',
      api_key: 'sk-ant-draft',
      base_url: 'https://anthropic-proxy.internal',
    });

    await user.click(combobox);
    expect(screen.getAllByRole('option')).toHaveLength(3);

    await user.type(combobox, 'opus');
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent('Claude Opus 5');

    await user.clear(combobox);
    await user.type(combobox, 'haiku');
    await user.click(screen.getByRole('option', { name: /claude haiku 4.5/i }));

    expect(screen.getByTestId('value')).toHaveTextContent('claude-haiku-4-5');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('supports keyboard selection', async () => {
    vi.mocked(plannerApi.listLLMModels).mockResolvedValue(
      response({
        models: [
          { id: 'gpt-4.1', label: 'gpt-4.1' },
          { id: 'gpt-4o-mini', label: 'gpt-4o-mini' },
        ],
      })
    );

    renderSelect({ provider: 'openai', apiKey: 'sk-test' });
    const user = userEvent.setup();

    await screen.findByText(/2 models available/i);
    const combobox = screen.getByRole('combobox');
    await user.click(combobox);
    await user.keyboard('{ArrowDown}{Enter}');

    expect(screen.getByTestId('value')).toHaveTextContent('gpt-4o-mini');
  });

  it('falls back to a plain text input when the backend reports an error', async () => {
    vi.mocked(plannerApi.listLLMModels).mockResolvedValue(
      response({ models: [], error: 'Anthropic rejected the API key (HTTP 401).' })
    );

    renderSelect({ initialValue: 'claude-sonnet-5' });
    const user = userEvent.setup();

    expect(await screen.findByText(MODEL_LIST_FALLBACK_NOTE)).toBeInTheDocument();
    expect(screen.getByText(/rejected the api key/i)).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();

    const input = screen.getByLabelText(/anthropic model/i);
    await user.clear(input);
    await user.type(input, 'claude-custom');
    expect(screen.getByTestId('value')).toHaveTextContent('claude-custom');
  });

  it('falls back to a plain text input when the request itself fails', async () => {
    vi.mocked(plannerApi.listLLMModels).mockRejectedValue(new Error('Request failed with status code 403'));

    renderSelect();

    expect(await screen.findByText(MODEL_LIST_FALLBACK_NOTE)).toBeInTheDocument();
    expect(screen.getByLabelText(/anthropic model/i)).toBeInTheDocument();
  });

  it('debounces refetches while the draft API key changes', async () => {
    vi.mocked(plannerApi.listLLMModels).mockResolvedValue(response({ models: [{ id: 'm', label: 'm' }] }));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <LLMModelSelect provider="openai" label="OpenAI Model" value="" onChange={() => undefined} apiKey="a" />
      </QueryClientProvider>
    );
    await screen.findByText(/1 model available/i);
    expect(plannerApi.listLLMModels).toHaveBeenCalledTimes(1);

    for (const key of ['ab', 'abc', 'abcd']) {
      view.rerender(
        <QueryClientProvider client={queryClient}>
          <LLMModelSelect provider="openai" label="OpenAI Model" value="" onChange={() => undefined} apiKey={key} />
        </QueryClientProvider>
      );
    }

    await waitFor(() => expect(plannerApi.listLLMModels).toHaveBeenCalledTimes(2));
    expect(plannerApi.listLLMModels).toHaveBeenLastCalledWith({ provider: 'openai', api_key: 'abcd', base_url: undefined });
  });

  it('never sends the previous key once Azure switches to Entra ID (JAN-349)', async () => {
    vi.mocked(plannerApi.listLLMModels).mockResolvedValue(response({ models: [{ id: 'chat', label: 'chat (gpt-4.1)' }] }));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const select = (authMode: 'entra' | 'api_key', apiKey: string) => (
      <QueryClientProvider client={queryClient}>
        <LLMModelSelect
          provider="azure_openai"
          label="Deployment Name"
          value=""
          onChange={() => undefined}
          apiKey={apiKey}
          baseUrl="https://x.openai.azure.com"
          authMode={authMode}
        />
      </QueryClientProvider>
    );
    const view = render(select('api_key', 'azure-key'));
    await screen.findByText(/1 model available/i);

    // The field clears the key in the same render that switches to Entra; the debounced key lags behind.
    view.rerender(select('entra', ''));

    await waitFor(() =>
      expect(plannerApi.listLLMModels).toHaveBeenLastCalledWith({
        provider: 'azure_openai',
        api_key: undefined,
        base_url: 'https://x.openai.azure.com',
        auth_mode: 'entra',
      })
    );
    const entraCalls = vi.mocked(plannerApi.listLLMModels).mock.calls.filter(([request]) => request.auth_mode === 'entra');
    expect(entraCalls.every(([request]) => request.api_key === undefined)).toBe(true);
  });

  it('resets debounced credentials and clears the stale model list when the provider changes mid-debounce', async () => {
    vi.mocked(plannerApi.listLLMModels).mockImplementation(async ({ provider }) =>
      response(
        provider === 'openai'
          ? { models: [{ id: 'gpt-4o-mini', label: 'gpt-4o-mini' }] }
          : { models: [{ id: 'claude-opus-5', label: 'Claude Opus 5' }] }
      )
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <LLMModelSelect provider="openai" label="OpenAI Model" value="" onChange={() => undefined} apiKey="sk-openai" />
      </QueryClientProvider>
    );
    await screen.findByText(/1 model available/i);

    // Switch provider and change the draft key in the same tick, as a fast admin switching
    // providers would — before the previous provider's 400ms debounce would have settled.
    view.rerender(
      <QueryClientProvider client={queryClient}>
        <LLMModelSelect provider="anthropic" label="Anthropic Model" value="" onChange={() => undefined} apiKey="sk-ant" />
      </QueryClientProvider>
    );

    // The stale OpenAI list must not still be shown/selectable while Anthropic's loads.
    expect(screen.getByRole('status')).toHaveTextContent(/loading available models/i);
    expect(screen.queryByText('gpt-4o-mini')).not.toBeInTheDocument();

    await screen.findByText(/1 model available/i);
    expect(plannerApi.listLLMModels).toHaveBeenLastCalledWith({
      provider: 'anthropic',
      api_key: 'sk-ant',
      base_url: undefined,
    });
  });
});
