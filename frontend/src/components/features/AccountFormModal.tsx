import React from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { Input } from '../ui/Input';
import type { EmailAccountCreate } from '../../types';

export type AccountForm = Omit<EmailAccountCreate, 'is_active'>;

interface AccountFormModalProps {
  isOpen: boolean;
  isEditing: boolean;
  isSaving: boolean;
  form: AccountForm;
  onChange: (form: AccountForm) => void;
  showPassword: boolean;
  onTogglePassword: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
  onClose: () => void;
}

/** The add / edit dialog of a custom IMAP account. */
export const AccountFormModal: React.FC<AccountFormModalProps> = ({
  isOpen,
  isEditing,
  isSaving,
  form,
  onChange,
  showPassword,
  onTogglePassword,
  onSubmit,
  onClose,
}) => (
  <Modal
    isOpen={isOpen}
    onClose={onClose}
    title={isEditing ? 'Edit Email Account' : 'Add a custom IMAP account'}
    className="max-w-lg"
  >
    <form onSubmit={onSubmit} className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <Input label="Account Name" required placeholder="Personal Gmail" value={form.name} onChange={e => onChange({ ...form, name: e.target.value })} />
        <Input label="Email Address" type="email" required placeholder="me@gmail.com" value={form.email} onChange={e => onChange({ ...form, email: e.target.value })} />
      </div>
      <div className="grid grid-cols-3 gap-4">
        <Input className="col-span-2" label="IMAP Server" required placeholder="imap.gmail.com" value={form.imap_server} onChange={e => onChange({ ...form, imap_server: e.target.value })} />
        <Input label="Port" type="number" required value={form.imap_port} onChange={e => onChange({ ...form, imap_port: parseInt(e.target.value) })} />
      </div>
      <Input label="Username" required placeholder="me@gmail.com" value={form.username} onChange={e => onChange({ ...form, username: e.target.value })} />
      <div className="relative">
        <Input
          label="Password / App Password"
          type={showPassword ? 'text' : 'password'}
          required={!isEditing}
          placeholder={isEditing ? 'Leave blank to keep the current password' : undefined}
          value={form.password}
          onChange={e => onChange({ ...form, password: e.target.value })}
        />
        <button type="button" onClick={onTogglePassword} className="absolute bottom-2.5 right-3 text-gray-400 hover:text-gray-600 transition-colors">
          {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
        </button>
      </div>
      <div className="flex gap-3">
        <Button type="submit" className="flex-1" isLoading={isSaving}>
          {isEditing ? 'Save Changes' : 'Save Account'}
        </Button>
        <Button type="button" variant="secondary" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </form>
  </Modal>
);
