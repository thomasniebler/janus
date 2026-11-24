import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { Combobox, type ComboboxOption } from './Combobox';

const models: ComboboxOption[] = [
  { value: 'llama3.1:8b', label: 'Llama 3.1 8B' },
  { value: 'llama3.1:70b', label: 'Llama 3.1 70B' },
  { value: 'mistral-nemo', label: 'Mistral Nemo' },
  { value: 'qwen2.5:14b', label: 'Qwen 2.5 14B' },
  { value: 'gpt-4o-mini', label: 'GPT-4o mini' },
];

const meta = {
  title: 'UI/Combobox',
  component: Combobox,
  args: {
    label: 'Model',
    value: 'llama3.1:8b',
    options: models,
    placeholder: 'Choose a model…',
    onChange: () => {},
  },
} satisfies Meta<typeof Combobox>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Wraps the args in local state so typing/selecting in the docs preview actually updates. */
const Interactive = (args: React.ComponentProps<typeof Combobox>) => {
  const [value, setValue] = useState(args.value);
  return <Combobox {...args} value={value} onChange={setValue} />;
};

export const Default: Story = {
  render: (args) => <Interactive {...args} />,
};

export const EmptyValue: Story = {
  args: { value: '', placeholder: 'Type or pick a model…' },
  render: (args) => <Interactive {...args} />,
};

export const FreeTextEntry: Story = {
  name: 'Free-text value not in the list',
  args: { value: 'custom-fine-tuned-model' },
  render: (args) => <Interactive {...args} />,
};

export const NoOptions: Story = {
  args: {
    options: [],
    value: '',
    emptyMessage: 'No models discovered yet. Test the connection first.',
  },
  render: (args) => <Interactive {...args} />,
};

export const WithHint: Story = {
  args: {
    hint: 'Fetched from the Ollama /api/tags endpoint.',
  },
  render: (args) => <Interactive {...args} />,
};
