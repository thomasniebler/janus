import type { Meta, StoryObj } from '@storybook/react';
import { AccountFormModal } from './AccountFormModal';

const form = {
  name: 'Personal Gmail',
  email: 'me@gmail.com',
  imap_server: 'imap.gmail.com',
  imap_port: 993,
  username: 'me@gmail.com',
  password: '',
};

const meta = {
  title: 'Features/AccountFormModal',
  component: AccountFormModal,
  args: {
    isOpen: true,
    isEditing: false,
    isSaving: false,
    form,
    onChange: () => undefined,
    showPassword: false,
    onTogglePassword: () => undefined,
    onSubmit: (event) => event.preventDefault(),
    onClose: () => undefined,
  },
} satisfies Meta<typeof AccountFormModal>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Adding: Story = {};

export const Editing: Story = { args: { isEditing: true } };

export const Saving: Story = { args: { isSaving: true } };
