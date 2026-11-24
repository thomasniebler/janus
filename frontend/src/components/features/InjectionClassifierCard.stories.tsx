import type { Meta, StoryObj } from '@storybook/react';
import { expect, userEvent, within } from 'storybook/test';
import { injectionClassifierApi } from '../../lib/injectionClassifier';
import type { InjectionClassifierStatus } from '../../lib/injectionClassifier';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { InjectionClassifierCard } from './InjectionClassifierCard';

const status = (overrides: Partial<InjectionClassifierStatus> = {}): InjectionClassifierStatus => ({
  enabled: false,
  model: 'ollama/llama3:latest (default)',
  is_cloud: false,
  monthly_token_budget: 200_000,
  period: '2026-10',
  tokens_used: 0,
  calls: 0,
  cached_verdicts: 0,
  ...overrides,
});

const seeded = (data: InjectionClassifierStatus) =>
  withQueryData((queryClient) => {
    queryClient.setQueryData(['injection-classifier'], data);
  });

const meta = {
  title: 'Features/InjectionClassifierCard',
  component: InjectionClassifierCard,
} satisfies Meta<typeof InjectionClassifierCard>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Fresh install with a local utility model: the check is off and nothing was used. */
export const LocalDisabled: Story = {
  decorators: [seeded(status())],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByLabelText('Model-based prompt-injection check')).not.toBeChecked();
    await expect(canvas.getByText('Off')).toBeInTheDocument();
    await expect(canvas.queryByText(/is a cloud model/)).not.toBeInTheDocument();
  },
};

/** On with a local model, after some checks this month. */
export const LocalEnabled: Story = {
  decorators: [seeded(status({ enabled: true, tokens_used: 18_400, calls: 42, cached_verdicts: 37 }))],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByLabelText('Model-based prompt-injection check')).toBeChecked();
    await expect(canvas.getByText(/18,400 of 200,000/)).toBeInTheDocument();
  },
};

/** A cloud utility model: a standing notice, and switching on asks first (Cancel keeps it off). */
export const CloudDisabled: Story = {
  decorators: [
    seeded(status({ is_cloud: true, model: 'openai/gpt-4o-mini' })),
    withApiOverride(injectionClassifierApi, 'update', () => neverResolving()),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText(/is a cloud model/)).toBeInTheDocument();
    await userEvent.click(canvas.getByLabelText('Model-based prompt-injection check'));
    const body = within(canvasElement.ownerDocument.body);
    const dialog = await body.findByRole('dialog', { name: 'Send suspicious outside text to a cloud model?' });
    await expect(dialog).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await expect(canvas.getByLabelText('Model-based prompt-injection check')).not.toBeChecked();
  },
};

/** On with a cloud model: the notice stays while it runs. */
export const CloudEnabled: Story = {
  decorators: [
    seeded(status({ enabled: true, is_cloud: true, model: 'openai/gpt-4o-mini', tokens_used: 9_600, calls: 12 })),
  ],
};

/** The monthly budget is spent: usage shows it, and checks fall back to the rules. */
export const BudgetSpent: Story = {
  decorators: [seeded(status({ enabled: true, monthly_token_budget: 10_000, tokens_used: 10_000, calls: 31 }))],
};

/** An invalid budget cannot be saved. */
export const InvalidBudget: Story = {
  decorators: [seeded(status({ enabled: true }))],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const budget = await canvas.findByLabelText('Monthly token budget');
    await userEvent.clear(budget);
    await expect(await canvas.findByText('Enter a whole number of tokens.')).toBeInTheDocument();
    await expect(canvas.getByRole('button', { name: 'Save' })).toBeDisabled();
  },
};

/** A save the server refuses shows its reason. */
export const SaveError: Story = {
  decorators: [
    seeded(status()),
    withApiOverride(injectionClassifierApi, 'update', () => Promise.reject(new Error('network error'))),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByLabelText('Model-based prompt-injection check'));
    await expect(await canvas.findByRole('alert')).toHaveTextContent('Saving failed.');
  },
};

/** Waiting for the status. */
export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(injectionClassifierApi, 'getStatus', () => neverResolving())],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('Loading the prompt-injection check…')).toBeInTheDocument();
  },
};

/** The server cannot be reached. */
export const LoadError: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(injectionClassifierApi, 'getStatus', () => Promise.reject(new Error('server error'))),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('Could not load the prompt-injection check settings.')).toBeInTheDocument();
  },
};
