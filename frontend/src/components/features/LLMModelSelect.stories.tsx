import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { plannerApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { LLMModelSelect } from './LLMModelSelect';
import type { LLMModelListResponse } from '../../types';

const anthropicModels: LLMModelListResponse = {
  models: [
    { id: 'claude-haiku-4-5', label: 'Claude Haiku 4.5' },
    { id: 'claude-opus-5', label: 'Claude Opus 5' },
    { id: 'claude-sonnet-5', label: 'Claude Sonnet 5' },
  ],
};

const seed =
  (key: unknown[], data: LLMModelListResponse) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(key, data);
  };

const meta = {
  title: 'Features/LLMModelSelect',
  component: LLMModelSelect,
  args: {
    provider: 'anthropic',
    label: 'Anthropic Model',
    value: 'claude-sonnet-5',
    onChange: () => {},
    placeholder: 'claude-sonnet-5',
    apiKey: 'sk-ant-draft',
  },
} satisfies Meta<typeof LLMModelSelect>;

export default meta;

type Story = StoryObj<typeof meta>;

export const ModelsAvailable: Story = {
  decorators: [withQueryData(seed(['llm-models', 'anthropic', 'sk-ant-draft', null], anthropicModels))],
};

export const Loading: Story = {
  decorators: [withApiOverride(plannerApi, 'listLLMModels', () => neverResolving())],
};

export const InvalidKeyFallback: Story = {
  args: { value: 'claude-sonnet-5' },
  decorators: [
    withQueryData(
      seed(['llm-models', 'anthropic', 'sk-ant-draft', null], {
        models: [],
        error: 'Anthropic rejected the API key (HTTP 401).',
      })
    ),
  ],
};

export const RequestFailedFallback: Story = {
  decorators: [withApiOverride(plannerApi, 'listLLMModels', () => Promise.reject(new Error('Request failed with status code 403')))],
};
