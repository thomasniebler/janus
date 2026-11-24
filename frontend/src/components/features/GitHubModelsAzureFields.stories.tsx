import { useState } from 'react';
import type { AxiosResponse } from 'axios';
import type { Meta, StoryObj } from '@storybook/react';
import { plannerApi } from '../../api';
import { withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import type { GitHubModelsAzureDraft } from '../../lib/llmProviderDrafts';
import type { LLMModelListRequest, LLMModelListResponse, PlannerSettings } from '../../types';
import { AzureOpenAIFields, GitHubModelsFields } from './GitHubModelsAzureFields';

const emptyDraft: GitHubModelsAzureDraft = {
  github_models_model: 'openai/gpt-4.1-mini',
  github_models_base_url: '',
  github_models_api_key: '',
  azure_openai_endpoint: '',
  azure_openai_deployment: '',
  azure_openai_api_version: '2024-10-21',
  azure_openai_auth_mode: 'entra',
  azure_openai_api_key: '',
};

const settingsWith = (configured: boolean) =>
  ({ github_models_api_key_configured: configured, azure_openai_api_key_configured: configured }) as PlannerSettings;

/** Holds the draft so the fields stay editable in the preview, like inside LLMConfig. */
const Harness = ({
  kind,
  initialDraft,
  configured,
}: {
  kind: 'github' | 'azure';
  initialDraft: Partial<GitHubModelsAzureDraft>;
  configured: boolean;
}) => {
  const [draft, setDraft] = useState<GitHubModelsAzureDraft>({ ...emptyDraft, ...initialDraft });
  const updateDraft = <K extends keyof GitHubModelsAzureDraft>(key: K, value: GitHubModelsAzureDraft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));
  const Fields = kind === 'github' ? GitHubModelsFields : AzureOpenAIFields;
  return <Fields draft={draft} settings={settingsWith(configured)} updateDraft={updateDraft} />;
};

/** The preview's model listing: Azure deployments (denied for the API key stories), GitHub models otherwise. */
export const listModelsPreview = async ({ provider, auth_mode }: LLMModelListRequest) =>
  ({
    data:
      provider === 'azure_openai'
        ? auth_mode === 'api_key'
          ? // An API key without the right to read deployments: the field falls back to free text.
            { models: [], error: 'Azure OpenAI denied listing deployments (HTTP 403, PermissionDenied).' }
          : {
              models: [
                { id: 'Kimi-K2.6', label: 'Kimi-K2.6 (Kimi-K2.6)' },
                { id: 'chat', label: 'chat (gpt-4.1-mini)' },
              ],
            }
        : {
            models: [
              { id: 'openai/gpt-4.1', label: 'OpenAI GPT-4.1' },
              { id: 'openai/gpt-4.1-mini', label: 'OpenAI GPT-4.1 mini' },
            ],
          },
  }) as AxiosResponse<LLMModelListResponse>;

const meta = {
  title: 'Features/GitHubModelsAzureFields',
  component: Harness,
  args: { kind: 'github', initialDraft: {}, configured: false },
  excludeStories: ['listModelsPreview'],
  decorators: [
    withQueryData(),
    withApiOverride(
      plannerApi,
      'listLLMModels',
      listModelsPreview as never
    ),
  ],
} satisfies Meta<typeof Harness>;

export default meta;

type Story = StoryObj<typeof meta>;

/** No token stored yet: the token field asks for a PAT or `gh auth token`. */
export const GitHubModelsMissingToken: Story = {};

/** A token is stored and a base URL override is set. */
export const GitHubModelsConfigured: Story = {
  args: { configured: true, initialDraft: { github_models_base_url: 'https://models.github.ai/inference' } },
};

/** Entra ID auth (the default): no key field, the host's Azure identity signs in and lists the deployments. */
export const AzureEntraId: Story = {
  args: {
    kind: 'azure',
    initialDraft: { azure_openai_endpoint: 'https://my-foundry.cognitiveservices.azure.com', azure_openai_deployment: 'Kimi-K2.6' },
  },
};

/** API key auth without a stored key: the key field is shown and empty; the listing is denied, so the
 * deployment falls back to free text. */
export const AzureApiKeyMissing: Story = {
  args: {
    kind: 'azure',
    initialDraft: {
      azure_openai_endpoint: 'https://my-resource.openai.azure.com',
      azure_openai_deployment: 'gpt-4.1-mini',
      azure_openai_auth_mode: 'api_key',
    },
  },
};

/** API key auth with a stored key: the placeholder says it can be replaced. */
export const AzureApiKeyConfigured: Story = {
  args: { ...AzureApiKeyMissing.args, configured: true },
};
