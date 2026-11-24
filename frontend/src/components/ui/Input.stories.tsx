import type { Meta, StoryObj } from '@storybook/react';
import { Input, Select } from './Input';

const meta = {
  title: 'UI/Input',
  component: Input,
  args: {
    label: 'Display name',
    placeholder: 'Ada Lovelace',
  },
} satisfies Meta<typeof Input>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const WithValue: Story = {
  args: {
    defaultValue: 'me@example.com',
    label: 'Email address',
  },
};

export const WithError: Story = {
  args: {
    label: 'IMAP password',
    type: 'password',
    error: 'This field is required',
  },
};

export const Disabled: Story = {
  args: {
    disabled: true,
    defaultValue: 'Cannot be changed',
  },
};

export const NoLabel: Story = {
  args: {
    label: undefined,
    placeholder: 'Search…',
  },
};

export const SelectField: Story = {
  render: () => (
    <Select label="Priority" defaultValue="important">
      <option value="important">Important</option>
      <option value="digest">Digest</option>
      <option value="ignore">Ignore</option>
      <option value="spam">Spam</option>
    </Select>
  ),
};

export const SelectWithError: Story = {
  render: () => (
    <Select label="Account" error="Select an account" defaultValue="">
      <option value="" disabled>
        Choose an account…
      </option>
      <option value="1">me@example.com</option>
    </Select>
  ),
};
