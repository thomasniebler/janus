import React, { useState } from 'react';
import axios from 'axios';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Scale, Plus, Trash2, RefreshCw, ChevronUp, ChevronDown } from 'lucide-react';
import { emailApi } from '../../api';
import { Card } from '../ui/Card';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { Input, Select } from '../ui/Input';
import type { Rule, RuleCreate } from '../../types';

type RuleForm = Omit<RuleCreate, 'is_active'>;

// A refused rule (e.g. an invalid or unsafe regex) used to leave the form open without
// a word (JAN-219); the backend's reason is shown in it instead. Validation errors (422)
// carry a list in `detail`; only a string is safe to render.
const ruleErrorMessage = (error: unknown): string => {
  const detail = axios.isAxiosError<{ detail?: unknown }>(error) ? error.response?.data?.detail : undefined;
  return typeof detail === 'string' ? detail : 'Could not save the rule.';
};

export const RuleManager: React.FC = () => {
  const queryClient = useQueryClient();
  const [showAddModal, setShowAddModal] = useState(false);
  const [form, setForm] = useState<RuleForm>({ name: '', pattern: '', field: 'sender', priority: 'important', order: 0 });

  const { data: rules, isLoading } = useQuery({
    queryKey: ['email-rules'],
    queryFn: async () => (await emailApi.getRules()).data,
  });

  const addMutation = useMutation({
    mutationFn: (data: RuleForm) => emailApi.addRule(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['email-rules'] });
      setShowAddModal(false);
      setForm({ name: '', pattern: '', field: 'sender', priority: 'important', order: 0 });
    },
  });

  const addError = addMutation.isError ? ruleErrorMessage(addMutation.error) : null;

  const closeAddModal = () => {
    setShowAddModal(false);
    addMutation.reset();
  };

  const reorderMutation = useMutation({
    mutationFn: ({ id, order }: { id: number; order: number }) => emailApi.updateRule(id, { order }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['email-rules'] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => emailApi.deleteRule(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['email-rules'] }),
  });

  const moveRule = (index: number, direction: 'up' | 'down') => {
    if (!rules) return;
    const sorted = [...rules].sort((a, b) => a.order - b.order);
    const swapIndex = direction === 'up' ? index - 1 : index + 1;
    if (swapIndex < 0 || swapIndex >= sorted.length) return;
    const current = sorted[index];
    const swap = sorted[swapIndex];
    reorderMutation.mutate({ id: current.id, order: swap.order });
    reorderMutation.mutate({ id: swap.id, order: current.order });
  };

  const sortedRules = rules ? [...rules].sort((a, b) => a.order - b.order) : [];

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-bold">Prioritization Rules</h2>
        <Button onClick={() => setShowAddModal(true)} icon={<Plus size={16} />}>New Rule</Button>
      </div>

      <div className="grid gap-4">
        {isLoading ? (
          <Card className="h-32 flex items-center justify-center">
            <RefreshCw className="animate-spin text-primary" />
          </Card>
        ) : sortedRules.length > 0 ? (
          sortedRules.map((rule: Rule, index: number) => (
            <Card key={rule.id} className="p-4 flex justify-between items-center">
              <div className="flex items-center gap-4">
                <div className="flex flex-col gap-0.5">
                  <button
                    onClick={() => moveRule(index, 'up')}
                    disabled={index === 0 || reorderMutation.isPending}
                    className="text-gray-400 hover:text-primary disabled:opacity-20 disabled:cursor-not-allowed"
                    aria-label="Move rule up"
                  >
                    <ChevronUp size={16} />
                  </button>
                  <button
                    onClick={() => moveRule(index, 'down')}
                    disabled={index === sortedRules.length - 1 || reorderMutation.isPending}
                    className="text-gray-400 hover:text-primary disabled:opacity-20 disabled:cursor-not-allowed"
                    aria-label="Move rule down"
                  >
                    <ChevronDown size={16} />
                  </button>
                </div>
                <div className="w-10 h-10 rounded-lg bg-accent/10 text-accent flex items-center justify-center">
                  <Scale size={20} />
                </div>
                <div>
                  <h3 className="font-bold text-sm">{rule.name}</h3>
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    If <span className="text-primary font-bold">{rule.field}</span> contains <span className="italic">"{rule.pattern}"</span> → <span className="text-accent font-bold uppercase">{rule.priority}</span>
                  </p>
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => deleteMutation.mutate(rule.id)}
                isLoading={deleteMutation.isPending && deleteMutation.variables === rule.id}
                aria-label={`Delete rule ${rule.name}`}
                title="Delete rule"
                icon={<Trash2 size={18} className="text-gray-400 hover:text-accent" />}
              />
            </Card>
          ))
        ) : (
          <div className="p-12 text-center bg-white dark:bg-dark-card rounded-2xl border border-dashed border-gray-200 dark:border-gray-700">
            <p className="text-gray-500 dark:text-gray-400 text-sm">No rules defined yet.</p>
          </div>
        )}
      </div>

      <Modal isOpen={showAddModal} onClose={closeAddModal} title="Add New Rule">
        <form onSubmit={(e) => { e.preventDefault(); addMutation.mutate(form); }} className="space-y-4">
          {addError && (
            <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-600 dark:bg-red-900/20 dark:text-red-400">
              {addError}
            </p>
          )}
          <Input
            label="Rule Name"
            required
            placeholder="e.g. Work Urgent"
            value={form.name}
            onChange={e => setForm({ ...form, name: e.target.value })}
          />
          <div className="grid grid-cols-2 gap-4">
            <Select label="Field" value={form.field} onChange={e => setForm({ ...form, field: e.target.value })}>
              <option value="sender">Sender</option>
              <option value="subject">Subject</option>
              <option value="snippet">Snippet</option>
            </Select>
            <Select
              label="Priority"
              value={form.priority}
              onChange={e => setForm({ ...form, priority: e.target.value as RuleForm['priority'] })}
            >
              <option value="important">Important</option>
              <option value="digest">Digest</option>
              <option value="ignore">Ignore</option>
              <option value="spam">Spam</option>
            </Select>
          </div>
          <Input
            label="Pattern (Regex or Keyword)"
            required
            placeholder="e.g. boss@company.com"
            value={form.pattern}
            onChange={e => setForm({ ...form, pattern: e.target.value })}
          />
          <Button type="submit" className="w-full" isLoading={addMutation.isPending}>Add Rule</Button>
        </form>
      </Modal>
    </div>
  );
};
