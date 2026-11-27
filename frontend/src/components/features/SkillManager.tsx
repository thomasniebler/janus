import { formatServerDateTime } from '../../lib/serverTime';
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Code2, Eye, Pencil, Plus, RefreshCw, Sparkles, Trash2 } from 'lucide-react';
import { skillApi } from '../../api';
import type { SkillCreate, SkillSummary, SkillUpdate } from '../../types';
import { SkillCatalogImport } from './SkillCatalogImport';
import { SkillSuggestions } from './SkillSuggestions';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';
import { Input } from '../ui/Input';
import { Modal } from '../ui/Modal';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';

const SKILL_NAME_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

type FormState = {
  name: string;
  description: string;
  license: string;
  content: string;
};

const EMPTY_FORM: FormState = {
  name: '',
  description: '',
  license: '',
  content: '',
};

type FormErrors = Partial<Record<keyof FormState, string>>;

/** The skill library is shared by the whole instance: everyone reads it, only admins change it. */
export const SkillManager: React.FC<{ isAdmin: boolean }> = ({ isAdmin }) => {
  const queryClient = useQueryClient();
  const { confirm, dialog } = useConfirmDialog();
  const [showModal, setShowModal] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [isLoadingSkillDetail, setIsLoadingSkillDetail] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [errors, setErrors] = useState<FormErrors>({});
  const [designGoal, setDesignGoal] = useState('');
  const [designError, setDesignError] = useState<string | null>(null);

  const {
    data: skills = [],
    isLoading: skillsLoading,
    isFetching: skillsFetching,
    refetch: refetchSkills,
  } = useQuery<SkillSummary[]>({
    queryKey: ['skills'],
    queryFn: async () => (await skillApi.getSkills()).data,
    refetchInterval: 30_000,
  });

  const createMutation = useMutation({
    mutationFn: (data: SkillCreate) => skillApi.createSkill(data),
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ['skills'] });
      queryClient.invalidateQueries({ queryKey: ['skills', response.data.id] });
      closeModal();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: SkillUpdate }) => skillApi.updateSkill(id, data),
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ['skills'] });
      queryClient.invalidateQueries({ queryKey: ['skills', response.data.id] });
      closeModal();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => skillApi.deleteSkill(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['skills'] });
    },
  });

  const designAssistMutation = useMutation({
    mutationFn: () =>
      skillApi.designAssist({
        goal: designGoal,
        draft_name: form.name,
        draft_description: form.description,
        draft_content: form.content,
      }),
    onSuccess: (response) => {
      setDesignError(null);
      setForm({
        name: response.data.suggested_name || form.name,
        description: response.data.suggested_description || form.description,
        license: form.license,
        content: response.data.suggested_content || form.content,
      });
    },
    onError: (error: Error) => {
      setDesignError(error.message);
    },
  });

  const validateForm = (): boolean => {
    const nextErrors: FormErrors = {};
    const name = form.name.trim();
    const content = form.content.trim();

    if (!name) {
      nextErrors.name = 'Name is required';
    } else if (!SKILL_NAME_PATTERN.test(name)) {
      nextErrors.name = 'Use lowercase letters, numbers, and hyphens only';
    }

    if (!content) {
      nextErrors.content = 'Content is required';
    }

    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  /** Clears the editor; ``goal`` prefills the designer (a skill suggestion passes its brief, JAN-407). */
  const resetEditor = (goal = '') => {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setErrors({});
    setDesignGoal(goal);
    setDesignError(null);
  };

  const closeModal = () => {
    setShowModal(false);
    setIsLoadingSkillDetail(false);
    resetEditor();
  };

  const openCreate = (goal = '') => {
    resetEditor(goal);
    setShowModal(true);
  };

  const openEdit = async (skillId: number) => {
    setEditingId(skillId);
    setErrors({});
    setShowModal(true);
    setIsLoadingSkillDetail(true);
    try {
      const detail = (await skillApi.getSkill(skillId)).data;
      queryClient.setQueryData(['skills', skillId], detail);
      setForm({
        name: detail.name,
        description: detail.description,
        license: detail.license ?? '',
        content: detail.content,
      });
      setDesignGoal('');
      setDesignError(null);
    } finally {
      setIsLoadingSkillDetail(false);
    }
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!validateForm()) return;

    const payload = {
      name: form.name.trim(),
      description: form.description.trim(),
      license: form.license.trim() || undefined,
      content: form.content.trim(),
    };

    if (editingId !== null) {
      updateMutation.mutate({ id: editingId, data: payload });
    } else {
      createMutation.mutate(payload);
    }
  };

  const isPending = createMutation.isPending || updateMutation.isPending;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xl font-bold flex items-center gap-2">
          <Code2 size={22} className="text-primary" />
          Skills
        </h2>
        <div className="flex items-center gap-2">
          <Button variant="ghost" onClick={() => void refetchSkills()} icon={<RefreshCw size={16} />}>
            Refresh
          </Button>
          {isAdmin ? (
            <Button onClick={() => openCreate()} icon={<Plus size={16} />}>
              New Skill
            </Button>
          ) : null}
        </div>
      </div>

      <p className="text-sm text-gray-500 dark:text-gray-400">
        Manage reusable skill instructions. Every skill applies to Janus in chat and in every trigger run.
        {isAdmin ? null : ' The library is shared by everyone on this instance, so only admins can change it.'}
      </p>
      {isAdmin ? <SkillSuggestions onDraft={openCreate} /> : null}
      {skillsFetching && !skillsLoading ? (
        <p className="text-xs text-gray-500 dark:text-gray-400">Refreshing skills list…</p>
      ) : null}

      {isAdmin ? <SkillCatalogImport /> : null}

      {skillsLoading ? (
        <Card className="h-32 flex items-center justify-center">
          <RefreshCw className="animate-spin text-primary" />
        </Card>
      ) : skills.length > 0 ? (
        <Card className="p-0 overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 dark:border-gray-800">
                <th className="text-left px-4 py-3 font-semibold">Name</th>
                <th className="text-left px-4 py-3 font-semibold">Description</th>
                <th className="text-left px-4 py-3 font-semibold">License</th>
                <th className="text-left px-4 py-3 font-semibold">Created</th>
                {isAdmin ? <th className="text-right px-4 py-3 font-semibold">Actions</th> : null}
              </tr>
            </thead>
            <tbody>
              {skills.map((skill) => (
                <tr key={skill.id} className="border-b border-gray-50 dark:border-gray-900/70 last:border-b-0">
                  <td className="px-4 py-3 font-medium">{skill.name}</td>
                  <td className="px-4 py-3 text-gray-600 dark:text-gray-300">{skill.description}</td>
                  <td className="px-4 py-3 text-gray-500 dark:text-gray-400">{skill.license || '—'}</td>
                  <td className="px-4 py-3 text-gray-500 dark:text-gray-400">{formatServerDateTime(skill.created_at)}</td>
                  {isAdmin ? (
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => openEdit(skill.id)}
                          aria-label={`Edit ${skill.name}`}
                          icon={<Pencil size={15} />}
                        />
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() =>
                            confirm({
                              title: 'Delete skill?',
                              message: `Are you sure you want to delete the skill "${skill.name}"?`,
                              confirmLabel: 'Delete',
                              tone: 'warning',
                              onConfirm: () => deleteMutation.mutate(skill.id),
                            })
                          }
                          isLoading={deleteMutation.isPending}
                          aria-label={`Delete ${skill.name}`}
                          icon={<Trash2 size={15} className="text-red-500" />}
                        />
                      </div>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : (
        <Card className="p-8 text-center">
          <Code2 size={32} className="mx-auto mb-3 text-gray-300 dark:text-gray-600" />
          <p className="text-sm font-medium text-gray-500 dark:text-gray-400">
            {isAdmin ? 'No skills yet. Create one to give Janus reusable instructions.' : 'No skills yet.'}
          </p>
        </Card>
      )}

      {showModal && (
        <Modal
          isOpen={showModal}
          title={editingId !== null ? 'Edit Skill' : 'New Skill'}
          onClose={closeModal}
          className="max-w-5xl"
        >
          {isLoadingSkillDetail ? (
            <div className="h-56 flex items-center justify-center">
              <RefreshCw className="animate-spin text-primary" />
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <Input
                  label="Name"
                  value={form.name}
                  onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                  placeholder="incident-response"
                  required
                  error={errors.name}
                />
                <Input
                  label="Description"
                  value={form.description}
                  onChange={(event) => setForm((prev) => ({ ...prev, description: event.target.value }))}
                  placeholder="Short purpose of this skill"
                  error={errors.description}
                />
                <Input
                  label="License"
                  value={form.license}
                  onChange={(event) => setForm((prev) => ({ ...prev, license: event.target.value }))}
                  placeholder="MIT"
                />
              </div>

              <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50 p-3 space-y-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 flex items-center gap-1">
                  <Sparkles size={12} />
                  AI skill design helper
                </p>
                <Input
                  label="What should this skill do?"
                  value={designGoal}
                  onChange={(event) => setDesignGoal(event.target.value)}
                  placeholder="E.g. triage production incidents and propose next steps"
                />
                <div className="flex justify-end">
                  <Button
                    type="button"
                    size="sm"
                    onClick={() => designAssistMutation.mutate()}
                    isLoading={designAssistMutation.isPending}
                    disabled={!designGoal.trim() || designAssistMutation.isPending}
                  >
                    Generate draft
                  </Button>
                </div>
                {designAssistMutation.data ? (
                  <div className="text-xs text-gray-600 dark:text-gray-300 space-y-2">
                    <p>{designAssistMutation.data.data.guidance}</p>
                    {designAssistMutation.data.data.follow_up_questions.length > 0 ? (
                      <ul className="list-disc ml-4 space-y-1">
                        {designAssistMutation.data.data.follow_up_questions.map((question) => (
                          <li key={question}>{question}</li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                ) : null}
                {designError ? <p className="text-xs text-red-500">{designError}</p> : null}
              </div>

              <div className="space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                    Skill content
                  </label>
                  <span
                    className="text-xs text-gray-500 dark:text-gray-400"
                    title="Skill instructions that reference tools (e.g. search_emails) only work when the matching integration is connected and the tool is allowed for the run."
                  >
                    ℹ Tool access reminder
                  </span>
                </div>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  Follow the{' '}
                  <a
                    href="https://agentskills.io"
                    target="_blank"
                    rel="noreferrer"
                    className="text-primary underline hover:no-underline"
                  >
                    agentskills.io format spec
                  </a>
                  . Skill instructions referencing tools (for example <code>search_emails</code>) only work when the
                  matching integration is connected and the tool is allowed for that run.
                </p>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div className="space-y-1">
                  <textarea
                    value={form.content}
                    onChange={(event) => setForm((prev) => ({ ...prev, content: event.target.value }))}
                    rows={14}
                    required
                    className="w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary/50 resize-y"
                    placeholder="# Skill instructions"
                  />
                  {errors.content ? <p className="text-[10px] font-bold text-red-500 ml-1 uppercase">{errors.content}</p> : null}
                </div>
                <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/60 p-4">
                  <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-3 flex items-center gap-1">
                    <Eye size={12} />
                    Preview
                  </p>
                  <article className="prose dark:prose-invert max-w-none prose-sm">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{form.content || '*No content yet.*'}</ReactMarkdown>
                  </article>
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <Button type="button" variant="ghost" onClick={closeModal}>
                  Cancel
                </Button>
                <Button type="submit" isLoading={isPending} disabled={isPending || isLoadingSkillDetail}>
                  {editingId !== null ? 'Save changes' : 'Create'}
                </Button>
              </div>
            </form>
          )}
        </Modal>
      )}
      {dialog}
    </div>
  );
};
